# ⚠ 2026-10-16 收尾补:主机侧轮次驱动。配置表在 CONFIGS,注释里写了每一项想回答什么。
#   两条必看的规矩:
#     · NEXT_SETTLE=NNN —— 每臂起跑前等到板子开机满 NNN 秒。**忘了设会让整轮作废**
#       (round7 就是这样:两臂起跑 up=526 vs up=44,"关实时"和"当时 SD 正忙"混在一起)
#     · 每臂起跑前 pre_probe() 把当前设置记到**主机侧** —— 板子一挂,板上那份日志
#       跟着没,panic_print / rcu_panic 这类设置就永远读不回来了
"""板子一回来就跑这一轮:把「哪一步把板子弄挂」夹出来。

上一轮已经知道的事(2026-10-16 晚):
  - 主机侧 soak 里 notebook 那条配置**连挂两次**;同一配置改成 LIVE_SECONDS=25
    (bisect_live.sh)四段全过。差别只在「永不退 / 拖滑杆 / SIGINT」这三样里。
  - 挂完之后板子有时候自己重启(panic=15),有时候双核全死要手按电源。

这一轮先把**次数**做出来:同一配置反复跑,记下每轮的 uptime,
看挂不挂和"开机多久了"有没有关系(上一轮两次挂分别在开机 6 分钟和 23 秒,
所以这个猜测本身还没站住,要靠次数说话)。

每一轮都:
  1. 重开一个黑匣子(独占一条 SSH 长连接,这样才能保证日志是新的一轮的;
     用 nohup/setsid 在板子上后台起 —— 试过,起不来,pid 是问号)
  2. 跑 sh /home/xilinx/one_run.sh "<额外环境变量>"
  3. 收不到 RESULT=done 就算挂;挂了等它重启,抢下 hangmon_sd.log

用法:
  python next_round.py <输出目录> <每个配置跑几轮> [配置名 ...]
  不给配置名就跑下面 CONFIGS 里全部。
"""

import os
import re
import sys
import threading
import time

import paramiko

HOST, USER, PASSWD = "192.168.2.99", "xilinx", "xilinx"
RUN_TIMEOUT = 200            # one_run.sh 本身 ~70 秒;给它留够
SETTLE = int(os.environ.get("NEXT_SETTLE", "0"))   # 每轮前等板子开机满多少秒
ABORT = False                # 板子挂了且一直没回来 → 收工(见 one_run 末尾)
# 挂了之后等它自己回来等几分钟。默认 45 —— 但带着 DUMP=1(panic 会自己重启)那几轮
# 其实 ~100 秒就回来了,没必要耗 45 分钟,用 NEXT_HANGWAIT=4 盖掉。
HANGWAIT = float(os.environ.get("NEXT_HANGWAIT", "45"))

# 名字 -> 传给 fir_live.py 的额外环境变量
CONFIGS = {
    # notebook 原样:wav + 10 Hz scope + 永不退 + 绑核(io→cpu0 dsp→cpu1)
    "A_notebook": "LIVE_SRC=wav LIVE_SCOPE=1",
    # 别的都不动,只把 mmc/eth/uart 的中断从 CPU0 挪到 CPU1
    # (搬样线程零余量地占着 CPU0,而这些中断默认全砸在 CPU0 上)
    "E_irq1": "LIVE_SRC=wav LIVE_SCOPE=1 IRQ1=1",
    # 只把 scope 从 10 Hz 降到 1 Hz(少 90% 的 /dev/shm 写 + 少很多 Python 唤醒)
    "B_slowscope": "LIVE_SRC=wav LIVE_SCOPE=1 LIVE_SCOPE_SEC=1.0 LIVE_STATUS=1.0",
    # 换音源:mic(区别是 select_microphone vs deselect_inputs 那几笔 I2C)
    "C_mic": "LIVE_SRC=mic LIVE_SCOPE=1",
    # 别的都跟 A 一样,只是**不拖滑杆**(不清 tune 文件、不写新的增益)
    "G_notune": "LIVE_SRC=wav LIVE_SCOPE=1 NOTUNE=1",
    # 完全不绑核、不开实时
    "D_nopin": "LIVE_SRC=wav LIVE_SCOPE=1 LIVE_PIN=0",
    # 停止时不走 stream_end 那道收尾,直接 kill -9(分辨"挂"在收尾路上还是本来已挂)
    "F_kill9": "LIVE_SRC=wav LIVE_SCOPE=1 STOP9=1",
    # A 之上多起一个硬件看门狗守卫(wd_guard.sh)。双核全死那一次 panic 都打不出来,
    # SWDT 是唯一能自动复位的路 —— 这一项既是在测它,也是从这轮起给 soak 上的保险。
    "H_wd": "LIVE_SRC=wav LIVE_SCOPE=1 WD=1",
    # **对照组**:管线照跑,但板子上**一个黑匣子都不开**,主机只 cat 内核文件。
    # 两次死亡的现场里,黑匣子自己也只跑了 9~35 秒,而 v3 每 6 秒 cp 到 SD、
    # 每 12 秒 sync、每秒还跑一次带 --sort=-pcpu 的 ps(最贵那条);
    # 成功 4/4 的那次 soak 没有黑匣子。所以"凶手是管线还是监控"必须分开问。
    "K_clean": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1",
    # K 之上再挂看门狗:万一它就是会挂,至少能自己爬回来,不用人按电源。
    "L_cleanwd": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 WD=1",
    # **直接量"能撑多久"**:不开黑匣子、不发信号,让它自己跑 300 秒。
    # 打点跑只能回答"挂没挂";按现有证据挂的概率大约是每连续 30~60 秒一次,
    # 所以"活到第几秒"才是真正要量的数。SELFW/TMOUT 都要盖过 LIVE_SECONDS。
    "N_clean": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 LIVE_SECONDS=300 NOSIG=1 "
               "SELFW=380 TMOUT=450",
    # N 之上再加一件事:**把 SD 卡从这 300 秒里摘掉**(先停 journald 再一次 sync 干净,
    # 跑完再拉起来)。挂的几轮现场都是 `sync`/`jbd2` 卡在 D、mmc0 中断冻住,而
    # fir_live.py 的 wav 只在启动时整份读进内存、运行时一块都不碰 SD ——
    # 所以往 SD 上压 IO 的**只能**是内核自己的写回。这一项和 N_clean 一比,
    # 就能回答"死机是不是 SD 写回引起的"。跑之前记得先有 N_clean 的数当基线。
    "P_sdquiet": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 NOJRNL=1 LIVE_SECONDS=300 "
                 "NOSIG=1 SELFW=380 TMOUT=450",
    # **抓现场那一轮**:notebook 原样,只加 DUMP=1 + FASTSTALL=1。板子一挂就在 panic
    # 那一下把所有任务的状态 + 内核栈打到串口上(panic_print=31),同时把 RCU stall 的
    # 判超时从 60 秒压到 10 秒(只为抓现场快,做 A/B 时不能开这个)。
    #
    # 为什么要绕这一下:串口里已经录到 **5 次 panic、0 次栈**。板上 /proc/config.gz
    # 查过 —— CONFIG_DETECT_HUNG_TASK / SOFTLOCKUP_DETECTOR / MAGIC_SYSRQ 全没编,
    # 没有 ftrace,连 /proc/<pid>/stack 都没有(没 CONFIG_STACKTRACE);而
    # `panic_on_rcu_stall=1` 是在 `rcu_dump_cpu_stacks()` **之前** panic 的,
    # 所以卡住那颗核的栈永远打不出来。`panic_print` 是唯一不受那条顺序影响的路。
    # **跑它的时候串口录制必须开着**,否则打出来了也没人接。
    # ✅ 2026-10-16 深夜(round6) 已抓到(round6/console.log,1492 行 / 633 帧)。
    "A_dump": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 FASTSTALL=1 "
              "LIVE_SECONDS=90 NOSIG=1 SELFW=180 TMOUT=240",
    # N_short = **RT 开着那条的对照组**,也是下面 R/S 两臂的共同基线。
    # 它和 A_dump 只差 FASTSTALL=1(判超时 60 秒 vs 10 秒),别的全一样 —— 所以
    # "它挂 / R_rtoff 不挂"这个比较里,唯一变的就是实时优先级本身。
    # DUMP=1 开着是白送的:不改行为,只在真挂了时留现场。
    "N_short": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 "
               "LIVE_SECONDS=90 NOSIG=1 SELFW=180 TMOUT=240",
    # **R_rtoff:把实时优先级关掉,别的一个字不改。**
    #
    # 这是 2026-10-16 深夜(round6) 那个根因的直接检验。全任务转储证明:挂的那一下
    # CPU0 上跑的正是搬样线程,它是**用户态**(`__irq_usr`),而 `ksoftirqd/0`
    # 和三个 CPU0 上的 worker 全是 `R` 却调度不上 —— 内核的延迟工作被这条
    # 不肯让出的 SCHED_FIFO 线程饿死了(内核线程是**普通 CFS 类**,不是实时)。
    # 于是 CPU0 上 RCU 的静默态永远报不上去 → stall → panic。
    # 不设实时,CPU0 就是 CFS 公平分,内核永远有份 → 那条链断掉。
    # 代价(10-15 实测过):丢块 0.26 → 0.54/秒,而设计本来就带交叠淡入,受得住。
    "R_rtoff": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 "
               "LIVE_RT_IO=0 LIVE_RT_DSP=0 "
               "LIVE_SECONDS=90 NOSIG=1 SELFW=180 TMOUT=240",
    # **S_jrnl2:实时照开,只把 SD 静音**(stop + runtime-mask journald 和所有 daily
    # 定时器 + sync + 关掉内核周期回刷)。上一版 P_sdquiet 就是因为只 stop 不 mask,
    # journald 10 秒内又活了,那一轮根本没静音、数据作废 —— 现在加了 settle 后的复检
    # (`nojrnl_check` 带 wr_ios/wr_ms,和 io_begin 相减就知道有没有干净)。
    #
    # 它要回答的是**另一条路**:饿死的机制是"5% 的窗口被 nice -20 的 mmc 工作抢走",
    # 那么把 SD 静音、让那 5% 全归 ksoftirqd,是不是就能**保住实时优先级**?
    # 如果能,演示就用它(音质不赔);如果不能,就只能上 R_rtoff。
    "S_jrnl2": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 NOJRNL=1 "
               "LIVE_SECONDS=90 NOSIG=1 SELFW=180 TMOUT=240",

    # ================= 2026-10-16 深夜(round7 之后重开的一轮)=================
    # round7 第 1 轮的教训:**`R_rtoff` 也挂**(而且是静默硬死),所以"关实时就能救"作废;
    # 但那一轮 `N_short` 起跑 up=526、`R_rtoff` 起跑 up=44,"关实时"和"开机风暴"混在一起,
    # 判不了。这一轮把两件事都修掉:
    #
    #  ① **起跑时机对齐** —— 用 `NEXT_SETTLE=180`(环境变量,不是配置里的)
    #  ② **改成量时长,不是数次数** —— 现在基线挂的概率约每 20 秒一次,打点跑("挂没挂")
    #     没有区分度;**跑满 300 秒不挂**才算过(300 秒里按基线该挂十几次)
    #  ③ `DUMP=1` 每一臂都开(不改行为,白留现场)
    #
    # 臂的顺序由 main 里 reps 控制:`reps=2` + `[N_long, G_long]` 会跑成
    # N G N G —— 交替跑,这样"机器随时间变坏"不会只算在其中一臂头上。
    #
    # N_long = 基线(实时开、拖滑杆),量它能撑多久。
    "N_long": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 "
              "LIVE_SECONDS=300 NOSIG=1 SELFW=380 TMOUT=450",
    # G_long = 与 N_long **只差一件事:不写旋钮文件**(NOTUNE=1)。
    # 为什么值得单列:round7 两臂都是在"拖滑杆"之后 4~12 秒死的
    # (N_short 10.6 秒时的状态里 `gain_applied_db` 还是 [0,0,0,0],
    #  R_rtoff 14 秒时已经是 [-19.87,-10,10,20] 且 `rt=0.92`)。
    # 写旋钮文件那条路会让 fir_live 重新往核里灌系数 —— 如果挂是这条引起的,
    # 演示的修法就是"别拖滑杆"(或把系数重载做得更温和),和用户的症状也对得上。
    # 它挂了(N_long 活)反而才是好消息。
    "G_long": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 NOTUNE=1 "
              "LIVE_SECONDS=300 NOSIG=1 SELFW=380 TMOUT=450",
    # R_long = 关实时(还是留一臂复核 round7 那个反常结果,这次在**对齐的起跑时机**下测)。
    "R_long": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 "
              "LIVE_RT_IO=0 LIVE_RT_DSP=0 "
              "LIVE_SECONDS=300 NOSIG=1 SELFW=380 TMOUT=450",
    # S_long = 实时照开 + 起跑前静音 SD。
    "S_long": "LIVE_SRC=wav LIVE_SCOPE=1 NOBOX=1 DUMP=1 NOJRNL=1 "
              "LIVE_SECONDS=300 NOSIG=1 SELFW=380 TMOUT=450",

    # **Z_idle:最重要的那个对照组 —— 板子什么都不跑,只闲 300 秒。**
    # 到目前为止,每一次"挂"都发生在跑管线的时候,所以"管线挂的"和"板子自己在坏"
    # 从来没分开过。这一臂就是不跑管线的对照组(one_run.sh 里 IDLE=1 那个分支,
    # 连 overlay 都不加载):
    #   · Z_idle 活 + 管线挂  ⇒ 锁定在管线/PL 这条路上
    #   · Z_idle 也挂        ⇒ 与我们一行代码无关,是机器自己(供电 / PL / SD 卡)
    # 判读时看 `idle_begin`/`idle_end` 两行中间板子有没有断线。
    "Z_idle": "NOBOX=1 IDLE=1 IDLE_S=300 DUMP=1 NOSIG=1 SELFW=380 TMOUT=450",
}

ONE_RUN_SH = None            # 本地路径,下面 main 里填


def connect(timeout=20):
    c = paramiko.SSHClient()
    c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    c.connect(HOST, username=USER, password=PASSWD,
              timeout=timeout, banner_timeout=timeout, auth_timeout=timeout)
    return c


def ssh_once(cmd, timeout=60):
    c = connect()
    try:
        _in, out, err = c.exec_command(cmd, timeout=timeout)
        return out.read().decode("utf-8", "replace") + \
            err.read().decode("utf-8", "replace")
    finally:
        c.close()


class Monitor:
    """独占一条 SSH 长连接跑 hangmon4.sh —— 连接一断,板子上那个脚本也跟着死。
    所以「重开黑匣子」= 关掉这条连接再开一条。

    v4 只写 /dev/shm(**一次都不碰 SD 卡**):v3 每 6 秒 cp 到 SD、每 12 秒 sync,
    而 v3 抓到的死亡现场恰好是 sync/jbd2 卡在 D 状态 —— 黑匣子自己可能就是
    往 SD 上压 IO 的那个人。代价是板子一重启内存就没了,所以下面配 Puller
    由主机把文件拉到本地。"""

    SH = "hangmon4.sh"

    def __init__(self):
        self.c = None

    def restart(self):
        self.stop()
        for _try in range(3):
            try:
                self.c = connect()
                self.c.exec_command("sh /home/xilinx/" + self.SH, timeout=None)
                return True
            except Exception:                                    # noqa: BLE001
                self.c = None
                time.sleep(3)
        return False

    def stop(self):
        if self.c is not None:
            try:
                self.c.close()
            except Exception:                                    # noqa: BLE001
                pass
            self.c = None


class Puller:
    """主机侧:每 2.5 秒把板子上那份日志抄一份到本地。

    板子挂掉/重启后内存里那份就没了 —— 只有拉下来过的那部分能留到事后。

    cmd 可换:开了黑匣子就拉 /dev/shm/hangmon.log;不开黑匣子(K_clean 那种
    对照组)就拉一份**最便宜**的现场 —— 只 cat 内核文件,不在板子上 fork
    ps/awk/top。板子上的活越少,越能分清"是管线挂的"还是"我的监控挂的"。
    """

    LIGHT = ("cat /proc/uptime; echo '--stat--'; cat /proc/stat; "
             "echo '--irq--'; cat /proc/interrupts; "
             "echo '--mem--'; head -3 /proc/meminfo; "
             "echo '--load--'; cat /proc/loadavg; "
             "echo '--io--'; cat /proc/pressure/io 2>&1; "
             "echo '--cpu--'; cat /proc/pressure/cpu 2>&1; "
             "echo '--disk--'; grep mmcblk0 /proc/diskstats; "
             "echo '--vm--'; grep -E '^(nr_dirty|nr_writeback|pgpgout|pgpgin)' "
             "/proc/vmstat; "
             "echo '--D--'; ps -eLo stat,psr,comm 2>/dev/null | grep -E '^D' | "
             "head -8; "
             "echo '--st--'; cat /dev/shm/fir_status.json 2>&1; "
             "echo; echo '--END--'")

    def __init__(self, path, cmd=None):
        self.path = path
        self.cmd = cmd or "cat /dev/shm/hangmon.log"
        # 每次尝试都往 .stamp 里追一行(成功/失败各一行,带主机时间)。
        # 为什么非要有它:快照文件只在**成功**时才被覆盖,所以"文件还停在 22:30"
        # 这件事分不清是"板子死了"还是"我的拉取器从一开始就没通"。
        # 有 .stamp 就能一眼看出最后成功是哪一秒、之后失败了多少次。
        self.stamp = path + ".stamp"
        with open(self.stamp, "w", encoding="utf-8") as f:
            f.write("[%s] 拉取器开始(约 3.5 秒一次)\n" % time.strftime("%H:%M:%S"))
        self.c = None
        self.go = True
        self.th = threading.Thread(target=self._loop, daemon=True)
        self.th.start()

    def _stamp(self, msg):
        try:
            with open(self.stamp, "a", encoding="utf-8") as f:
                f.write("[%s] %s\n" % (time.strftime("%H:%M:%S"), msg[:140]))
        except Exception:                                        # noqa: BLE001
            pass

    def _loop(self):
        while self.go:
            try:
                if self.c is None:
                    self.c = connect(timeout=8)
                _in, out, _err = self.c.exec_command(self.cmd, timeout=10)
                data = out.read()
                if data:
                    with open(self.path, "wb") as f:
                        f.write(data)
                    self._stamp("ok " + data.split(b"\n")[0]
                                .decode("utf-8", "replace").strip())
                else:
                    self._stamp("通了但没内容")
            except Exception as e:                               # noqa: BLE001
                self._stamp("失败 %s: %s" % (type(e).__name__, e))
                try:
                    if self.c is not None:
                        self.c.close()
                except Exception:                                # noqa: BLE001
                    pass
                self.c = None
                time.sleep(3)
            time.sleep(2.5)

    def stop(self):
        self.go = False
        try:
            if self.c is not None:
                self.c.close()
        except Exception:                                        # noqa: BLE001
            pass


def uptime():
    try:
        return int(float(ssh_once("cut -d. -f1 /proc/uptime", timeout=25).strip()))
    except Exception:                                            # noqa: BLE001
        return -1


def log(outdir, msg):
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    print(line)
    sys.stdout.flush()
    with open(os.path.join(outdir, "round_host.log"), "a", encoding="utf-8") as f:
        f.write(line + "\n")


def wait_up(outdir, mins=6.0):
    t0 = time.time()
    while time.time() - t0 < mins * 60:
        if uptime() >= 0:
            log(outdir, "  板子回来了(等了 %d 秒)" % (time.time() - t0))
            time.sleep(20)                       # 等 sshd 之类起齐
            return True
        time.sleep(10)
    log(outdir, "  等了 %d 分钟板子还是没回来 —— 大概要手按电源" % mins)
    return False


def push_file(local, remote, make_exec=True):
    import base64
    with open(local, "rb") as f:
        b64 = base64.b64encode(f.read().replace(b"\r\n", b"\n"))
    c = connect()
    try:
        _in, out, err = c.exec_command(
            "cat | base64 -d > %s && chmod %s %s && echo PUSH_OK"
            % (remote, "+x" if make_exec else "644", remote), timeout=60)
        _in.write(b64)
        _in.channel.shutdown_write()
        r = out.read().decode("utf-8", "replace")
        return "PUSH_OK" in r
    finally:
        c.close()


def grab(outdir, tag):
    try:
        data = ssh_once(
            "echo '##### sd'; cat /home/xilinx/hangmon_sd.log 2>&1; "
            "echo '##### shm'; tail -60 /dev/shm/hangmon.log 2>&1; "
            "echo '##### one_run'; cat /home/xilinx/one_run.log 2>&1; "
            "echo '##### uptime'; cat /proc/uptime; "
            "echo '##### END'", timeout=90)
        p = os.path.join(outdir, "grab_%s.txt" % tag)
        with open(p, "w", encoding="utf-8") as f:
            f.write(data)
        log(outdir, "  黑匣子 -> %s (%d bytes)" % (p, len(data)))
    except Exception as e:                                       # noqa: BLE001
        log(outdir, "  抢日志失败: %s" % e)


def pre_probe(outdir, tag, i):
    """每臂起跑前,把"这一臂到底是在什么设置下跑的"记到**主机侧**日志里。

    为什么必须记在主机侧:板子一挂,板上 one_run.log 跟着没,`dump:` 那一行
    (写着 `panic_print` / `rcu_panic` / `panic`)就永远读不到了 —— round7 的
    `R_rtoff` 就是这样,我们到现在都不知道它跑的时候 `panic_on_rcu_stall` 还在不在。
    而这一条直接决定那次静默死亡怎么解释:如果 `rcu_panic=0`,那"没打转储"就只是
    因为没设,不代表"没停"。
    """
    try:
        s = ssh_once(
            "cut -d. -f1 /proc/uptime; "
            "printf 'panic='; cat /proc/sys/kernel/panic; "
            "printf 'rcu_panic='; cat /proc/sys/kernel/panic_on_rcu_stall 2>&1; "
            "printf 'panic_print='; cat /proc/sys/kernel/panic_print 2>&1; "
            "printf 'stall_timeout='; "
            "cat /sys/module/rcupdate/parameters/rcu_cpu_stall_timeout 2>&1; "
            "printf 'rt_runtime_us='; cat /proc/sys/kernel/sched_rt_runtime_us; "
            "printf 'journald='; systemctl is-active systemd-journald 2>&1; "
            "printf 'wbcs='; cat /proc/sys/vm/dirty_writeback_centisecs; "
            "grep -E '^ *179 +0 mmcblk0 ' /proc/diskstats; "
            # SD 卡那本账,每臂起跑前记一次(主机侧)。三条都是以前从没读过的:
            #   mmcerr  = SDHCI 自己数的命令超时/CRC 错 —— 卡/总线在硬件层坏没坏
            #   ext4err = ext4 累计报过几次内部错 + 最近一次是什么函数哪一行
            #   ltkb    = 这个文件系统**有生以来**被写了多少 KB(磨损)
            "printf 'mmcerr='; echo xilinx | sudo -S cat /sys/kernel/debug/mmc0/"
            "err_stats 2>&1 | tr '\\n' ','; "
            "printf 'ext4err='; echo xilinx | sudo -S cat "
            "/sys/fs/ext4/mmcblk0p2/errors_count 2>&1; "
            "printf 'ltkb='; echo xilinx | sudo -S cat "
            "/sys/fs/ext4/mmcblk0p2/lifetime_write_kbytes 2>&1; "
            "cat /proc/loadavg", timeout=60)
        log(outdir, "  [%s_%d] 前置: %s"
            % (tag, i, " | ".join(x.strip() for x in s.splitlines() if x.strip())))
    except Exception as e:                                       # noqa: BLE001
        log(outdir, "  [%s_%d] 前置读不到: %s: %s"
            % (tag, i, type(e).__name__, e))


def one_run(outdir, mon, tag, extra, i):
    global ABORT
    if SETTLE:
        t0 = time.time()
        while time.time() - t0 < 900:
            u = uptime()
            if u < 0:
                # 板子不在(上一臂挂了正在重启)。**别把 u<0 当成"到了"就往下跑** ——
                # 那样会在一条死板子上"跑"出一次假 HANG,把账算歪。
                if not wait_up(outdir, mins=4.0):
                    log(outdir, "  SETTLE 等不到板子,收工")
                    ABORT = True
                    return "HANG", -1
                continue
            if u >= SETTLE:
                break
            time.sleep(10)
        log(outdir, "  settle 等到了 up=%d(目标 %d)" % (uptime(), SETTLE))

    up0 = uptime()
    nobox = " NOBOX=1 " in " %s " % extra
    pre_probe(outdir, tag, i)

    # NOBOX=1:板子上不开黑匣子(连一次 /proc 快照循环都不跑),主机侧只 cat 内核文件。
    # 这是"凶手是管线还是我的监控"的对照组 —— 别把这条和 A 的结果混着看。
    if nobox:
        log(outdir, "  up=%s 黑匣子=按 NOBOX 关掉了" % up0)
        mon.stop()
    else:
        log(outdir, "  up=%s 起黑匣子=%s" % (up0, mon.restart()))

    # 主机侧同步拉现场:板子一挂内存就没了,只有拉下来的才留得住
    pull = Puller(os.path.join(outdir, "mon_%s_%d.log" % (tag, i)),
                  Puller.LIGHT if nobox else None)

    verdict = "HANG"
    try:
        c = connect()
        # 长跑配置(N_clean 那种)要在 extra 里带 TMOUT=xxx 把这条链路的超时顶上去,
        # 不然 300 秒的跑会在 200 秒被自己的客户端掐掉,误报成 HANG。
        m = re.search(r"TMOUT=(\d+)", extra)
        tmo = int(m.group(1)) if m else RUN_TIMEOUT
        try:
            _in, out, err = c.exec_command(
                'sh /home/xilinx/one_run.sh "%s" 8' % extra, timeout=tmo)
            data = out.read().decode("utf-8", "replace")
        finally:
            c.close()
        with open(os.path.join(outdir, "run_%s_%d.txt" % (tag, i)), "w",
                  encoding="utf-8") as f:
            f.write(data)
        if "RESULT=done" in data:
            verdict = "PASS" if "ALIVE_AFTER_STOP=n" in data else "PASS_LIMP"
            for ln in data.splitlines():
                if ln.startswith(("after_tune", "threads_at_stop",
                                  "self_exit", "after_stop",
                                  "io_begin", "io_end", "nojrnl", "dump",
                                  "faststall")):
                    log(outdir, "    %s" % ln[:200])
    except Exception as e:                                       # noqa: BLE001
        log(outdir, "  SSH 断了: %s: %s" % (type(e).__name__, e))
    time.sleep(3)                     # 让 puller 把最后一段抄走
    pull.stop()

    up1 = uptime()
    log(outdir, "  => %s  (up %s -> %s)" % (verdict, up0, up1))

    if verdict == "HANG":
        mon.stop()
        # 挂了之后要么它自己 panic+重启(开着 panic=15,约 100 秒回来),
        # 要么就得人按电源。按电源不由我控制。等不到就**收工**,而不是拿着一条死板子
        # 把后面的配置一个个"跑"成假 HANG —— 那种记录会把账算歪。
        if wait_up(outdir, mins=HANGWAIT):
            grab(outdir, "%s_%d" % (tag, i))
        else:
            ABORT = True
            log(outdir, "  板子一直没回来 —— 需要人按电源。本轮收工")
    return verdict, up0


def main():
    outdir = sys.argv[1]
    reps = int(sys.argv[2])
    names = sys.argv[3:] or list(CONFIGS)
    os.makedirs(outdir, exist_ok=True)
    log(outdir, "ROUND BEGIN  reps=%d  configs=%s  settle=%d"
        % (reps, names, SETTLE))

    # 等板子(它可能还挂着,要人按电源)。默认等 10 分钟;
    # `NEXT_WAITMIN=180` 可以让它一直等 —— 板子硬死时只能人按电源,
    # 而人不在旁边,所以这一条让"按了电源就自动开跑"。
    if not wait_up(outdir, mins=float(os.environ.get("NEXT_WAITMIN", "10"))):
        log(outdir, "板子起不来,收工")
        return 1

    here = os.path.dirname(os.path.abspath(__file__))
    ok = True
    for fn in ("one_run.sh", "hangmon4.sh", "wd_guard.sh"):
        if push_file(os.path.join(here, fn), "/home/xilinx/" + fn):
            log(outdir, "%s 推上去了" % fn)
        else:
            log(outdir, "%s 推失败" % fn)
            ok = False
    if not ok:
        log(outdir, "收工")
        return 1

    mon = Monitor()
    tally = {}
    rows = []
    try:
        for i in range(1, reps + 1):
            for name in names:
                extra = CONFIGS.get(name, name)      # 也允许直接传环境变量串
                log(outdir, "--- %s 第 %d 轮 ---" % (name, i))
                v, up0 = one_run(outdir, mon, name, extra, i)
                tally.setdefault(name, []).append(v)
                rows.append((name, i, v, up0))
                if ABORT:
                    break
            if ABORT:
                break
    finally:
        mon.stop()

    log(outdir, "===== 汇总 =====")
    for name in names:
        vs = tally.get(name, [])
        log(outdir, "  %-14s %s   (挂 %d/%d)"
            % (name, " ".join(vs), sum(1 for x in vs if x == "HANG"), len(vs)))
    log(outdir, "ROUND DONE")
    return 0


if __name__ == "__main__":
    sys.exit(main())
