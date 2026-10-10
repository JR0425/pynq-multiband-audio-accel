/*
 * audio_stream.cpp —— 给 PYNQ-Z2 的音频 IP 补一条**能连续跑**的播放/录音通路。
 *
 * 为什么要自己编这个
 * ==================
 * `pynq.lib.audio` 的 `record()` / `play()` 是**一次一块**的设计，
 * 而且 `play()` 每调一次都顺手做一遍"开声 / 静音"：
 *
 *     setUIO(); setI2C();
 *     write_audio_reg(R22, 0x21);  write_audio_reg(R24, 0x41);   ← 4 次 I2C 写：开声
 *     write_audio_reg(R29, vol);   write_audio_reg(R30, vol);
 *     for (i...) { 搬一个采样 }                                  ← 只有这段是干活
 *     write_audio_reg(R22, 0x01);  write_audio_reg(R24, 0x01);   ← 4 次 I2C 写：静音
 *     write_audio_reg(R23, 0x00);  write_audio_reg(R25, 0x00);
 *     unsetUIO(); unsetI2C();
 *
 * 实测（board/scripts/audio_overhead_probe.py）：
 *
 *     record(nsamples=0)   0.119 ms   只有 mmap + 开 i2c
 *     play  (nsamples=0)   3.447 ms   再多 8 次 I2C 写
 *     差                   3.329 ms   ← 每次调用白花的固定开销
 *
 * 传数据的循环本身受 I2S 时钟节拍限制，正好是 L/48000 秒，不额外花钱；
 * **贵的是那 8 次 I2C 写**（每次约 416 µs）。它和块长无关，所以块长再怎么调
 * 也摊不掉：每播 1 秒音频要花 `1 + 166/L` 秒，永远到不了 1。
 *
 * 更糟的是这 8 次写里有 4 次是**把 DAC 静音**：按 L=480 算，每秒静音再开声
 * 100 次 —— 那是会直接听见咔声的。
 *
 * 另外 `uio.c` 的 `setUIO()` 只 `open()` 从 `close()`，**每调一次漏一个文件描述符**，
 * 每秒 100 次调用就是每秒漏 100 个。这里一并修掉。
 *
 * 这个文件做什么
 * ==============
 * 把"一次一块"拆成"一个会话 + 很多块"：
 *
 *     stream_begin()   mmap + 开 i2c + **开声一次**
 *     stream_block()   只搬数据，一次 I2C 都不写     ← 可以每 10 ms 调一次
 *     stream_end()     **静音一次** + munmap + close
 *
 * 录音那边同样拆（`capture_*`），顺带省掉它每次 mmap + 开 i2c 的 0.119 ms。
 * 录音本来就不写 I2C，所以那三个函数里没有 `iic_fd`。
 *
 * **算法一个字没动，PL 里的 bit 也不用重跑。** 改的只是 PS 侧怎么喂数据。
 *
 * 怎么编
 * ======
 *     board/scripts/build_audio_stream.sh
 * （板上就有 gcc 9.3.0，驱动源码在 pynq 包里，不用交叉编译）
 *
 * 怎么用（Python 侧）
 * ==================
 *     ffi = cffi.FFI()
 *     ffi.cdef("""
 *         void* stream_begin(unsigned int mmap_size, unsigned int volume,
 *                            int uio_index, int iic_index);
 *         int   stream_block(void* h, unsigned int* buf, unsigned int nsamples);
 *         void  stream_end(void* h);
 *         void* capture_begin(unsigned int mmap_size, int uio_index);
 *         int   capture_block(void* h, unsigned int* buf, unsigned int nsamples);
 *         void  capture_end(void* h);
 *         int   duplex_block(void* h, unsigned int* in_buf,
 *                            unsigned int* out_buf, unsigned int nsamples);
 *     """)
 *     lib = ffi.dlopen("<...>/libaudio_stream.so")
 *
 * `duplex_block` 用的是 `stream_begin` 返回的句柄（那条已经开好声、开着 i2c），
 * 一个调用里既收又放 —— 详见文件末尾那段注释。要在两个核上摆"搬样 + 过核"
 * 两件事时用它，别用 capture_block + stream_block 那两个（各占一个核）。
 *
 * 寄存器约定抄自 `_pynq/_audio/audio_adau1761.h`（板上那份，没猜）：
 *     I2S_DATA_RX_L_REG 0x00   I2S_DATA_RX_R_REG 0x04
 *     I2S_DATA_TX_L_REG 0x08   I2S_DATA_TX_R_REG 0x0C
 *     I2S_STATUS_REG    0x10
 */

#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <fcntl.h>
#include <unistd.h>
#include <sys/mman.h>

#include "audio_adau1761.h"

/*
 * ⚠️ 这两个是**用 C 编的**（`i2cps.c`，走 gcc 而不是 g++），所以符号名没有被
 * C++ 改写。在这里必须显式声明成 `extern "C"`，否则链接出来的 .so 会报
 * `undefined symbol: _Z8unsetI2Ci`（= 被改写过的 `unsetI2C(int)`），
 * 而 `i2cps.c` 里明明有这个函数。
 *
 * 故意不写 `#include "i2cps.h"` —— 那个头文件里还牵着 `<linux/i2c-dev.h>`，
 * 塞进 extern "C" 里容易连带出别的麻烦。这里只用到两个函数，直接声明更干净。
 * （pynq 自己的 `audio_adau1761.cpp` 是把整个头文件都包在 `extern "C"` 里的，
 *   所以它没踩这个坑。）
 */
extern "C" int setI2C(unsigned int index, long slave_addr);
extern "C" int unsetI2C(int i2c_fd);

/* 这个是 C++ 编的（`audio_adau1761.cpp`），照常按 C++ 声明即可。 */
extern void write_audio_reg(unsigned char u8RegAddr, unsigned char u8Data,
                            int iic_fd);

/*
 * 状态寄存器的轮询加一个上限。
 * 原版是个 `while (status == 0);` 的裸循环 —— 万一 I2S 那边不吐状态了
 * （codec 没配好、时钟没起来），调用线程就**永远出不来**，
 * 主线程 join 直接挂死，还看不到任何报错。这里给它一个上限，
 * 超了就提前返回"实际搬了多少个"，让 Python 能看出来并收摊。
 *
 * 每次迭代就一次 volatile 读 + 一个自增，纳秒量级；
 * 一个采样周期是 20.83 µs，所以这个上限大约是 0.1~0.4 秒，
 * 比该等的时间长几个数量级，又短到不会真挂住。
 */
static const unsigned long MAX_SPIN = 100000000UL;

#define AUDIO_STREAM_MAGIC 0xA5D10C01u

typedef struct {
    unsigned int magic;
    int          uio_fd;
    void*        uio_ptr;
    unsigned int mmap_size;
    int          iic_fd;      /* 录音那边不用，置 -1 */
} audio_stream_t;


/* ---------------------------------------------------------------- 公共 ---- */

static audio_stream_t* new_handle(unsigned int mmap_size, int uio_index)
{
    audio_stream_t* s = (audio_stream_t*)calloc(1, sizeof(audio_stream_t));
    if (s == NULL) {
        printf("audio_stream: calloc failed\n");
        return NULL;
    }
    s->magic     = AUDIO_STREAM_MAGIC;
    s->mmap_size = mmap_size;
    s->iic_fd    = -1;

    char path[32];
    snprintf(path, sizeof(path), "/dev/uio%d", uio_index);
    s->uio_fd = open(path, O_RDWR);
    if (s->uio_fd < 0) {
        printf("audio_stream: cannot open %s\n", path);
        free(s);
        return NULL;
    }
    /* 注意：这里自己 mmap，不走 uio.c 的 setUIO()。
       setUIO() open() 完不 close，每调一次漏一个 fd —— 我们不能漏。 */
    s->uio_ptr = mmap(NULL, mmap_size, PROT_READ | PROT_WRITE, MAP_SHARED,
                      s->uio_fd, 0);
    if (s->uio_ptr == MAP_FAILED) {
        printf("audio_stream: mmap failed\n");
        close(s->uio_fd);
        free(s);
        return NULL;
    }
    return s;
}

static void free_handle(audio_stream_t* s)
{
    if (s == NULL || s->magic != AUDIO_STREAM_MAGIC) {
        return;
    }
    if (s->uio_ptr != NULL && s->uio_ptr != MAP_FAILED) {
        munmap(s->uio_ptr, s->mmap_size);
    }
    if (s->uio_fd >= 0) {
        close(s->uio_fd);
    }
    if (s->iic_fd >= 0) {
        unsetI2C(s->iic_fd);
    }
    s->magic = 0;
    free(s);
}


/* ---------------------------------------------------------------- 播放 ---- */

/*
 * 开声。只做一次。
 * 这一段和原版 play() 开头那 4 次 write_audio_reg 一模一样（值也一样）。
 */
extern "C" void* stream_begin(unsigned int mmap_size, unsigned int volume,
                              int uio_index, int iic_index)
{
    audio_stream_t* s = new_handle(mmap_size, uio_index);
    if (s == NULL) {
        return NULL;
    }
    s->iic_fd = setI2C((unsigned int)iic_index, IIC_SLAVE_ADDR);
    if (s->iic_fd < 0) {
        printf("audio_stream: cannot open i2c %d\n", iic_index);
        free_handle(s);
        return NULL;
    }

    unsigned char vol_register = (unsigned char)((volume << 2) | 0x3);
    /* 解除左右声道 DAC 的静音，并打开 Mixer3/Mixer4 */
    write_audio_reg(R22_PLAYBACK_MIXER_LEFT_CONTROL_0,  0x21, s->iic_fd);
    write_audio_reg(R24_PLAYBACK_MIXER_RIGHT_CONTROL_0, 0x41, s->iic_fd);
    /* 打开左右声道耳机输出，并设音量 */
    write_audio_reg(R29_PLAYBACK_HEADPHONE_LEFT_VOLUME_CONTROL,
                    vol_register, s->iic_fd);
    write_audio_reg(R30_PLAYBACK_HEADPHONE_RIGHT_VOLUME_CONTROL,
                    vol_register, s->iic_fd);
    return (void*)s;
}

/*
 * 推一块出去。
 * 返回**实际推进去的采样数**。正常情况下等于 nsamples；
 * 小于它说明 I2S 那边等超时了（见 MAX_SPIN 那段注释）。
 */
extern "C" int stream_block(void* h, unsigned int* BufAddr,
                            unsigned int nsamples)
{
    audio_stream_t* s = (audio_stream_t*)h;
    if (s == NULL || s->magic != AUDIO_STREAM_MAGIC) {
        return -1;
    }
    uint8_t* base = (uint8_t*)s->uio_ptr;

    for (unsigned int i = 0; i < nsamples; i++) {
        volatile unsigned int* status_reg =
            (volatile unsigned int*)(base + I2S_STATUS_REG);
        unsigned long guard = 0;
        while (*status_reg == 0) {
            if (++guard > MAX_SPIN) {
                return (int)i;          /* 等超时了，提前收 */
            }
        }
        *status_reg = 0x00000001;       /* 应答，告诉硬件这个采样取走了 */

        *((volatile int*)(base + I2S_DATA_TX_L_REG)) = (int)(*(BufAddr + 2 * i));
        *((volatile int*)(base + I2S_DATA_TX_R_REG)) = (int)(*(BufAddr + 2 * i + 1));
    }
    return (int)nsamples;
}

/*
 * 静音 + 收摊。只做一次。
 * 和原版 play() 结尾那 4 次写一样。
 */
extern "C" void stream_end(void* h)
{
    audio_stream_t* s = (audio_stream_t*)h;
    if (s == NULL || s->magic != AUDIO_STREAM_MAGIC) {
        return;
    }
    if (s->iic_fd >= 0) {
        /* 静音左右声道 DAC */
        write_audio_reg(R22_PLAYBACK_MIXER_LEFT_CONTROL_0,  0x01, s->iic_fd);
        write_audio_reg(R24_PLAYBACK_MIXER_RIGHT_CONTROL_0, 0x01, s->iic_fd);
        /* 关掉 Mixer3 的左输入和 Mixer4 的右输入 */
        write_audio_reg(R23_PLAYBACK_MIXER_LEFT_CONTROL_1,  0x00, s->iic_fd);
        write_audio_reg(R25_PLAYBACK_MIXER_RIGHT_CONTROL_1, 0x00, s->iic_fd);
    }
    free_handle(s);
}

/*
 * 单独写一个 codec 寄存器。
 *
 * 为什么留这个口子：`stream_begin` 那 4 次 I2C 写会把**麦克风输入**推成满幅
 * 自激。2026-10-10 实测的分辨实验（cap_probe2.py）：
 *
 *     段1  什么都不写，只收      原始 6e4~1.1e5，16 位域 rms 230   ＝ 安静
 *     段2  只做那 4 次 I2C 写，再收  原始 0~2^24 整幅乱撞，rms 46551
 *     段3  TX 全填 0 的 duplex      同上，rms 45843
 *
 * 也就是**跟 TX 发什么、跟输出多大都无关**，就是那 4 次写。
 * 要定位到具体是哪一位、哪一次，就得能单独 poke。
 *
 * 用法（Python，cffi）：先 `audio.select_microphone()`，再
 *     lib.stream_reg_write(0x1C, 0x21, audio.iic_index)
 */
extern "C" int stream_reg_write(unsigned char reg, unsigned char val,
                                int iic_index)
{
    int fd = setI2C((unsigned int)iic_index, IIC_SLAVE_ADDR);
    if (fd < 0) {
        printf("audio_stream: stream_reg_write 开不了 i2c %d\n", iic_index);
        return -1;
    }
    write_audio_reg(reg, val, fd);
    unsetI2C(fd);
    return 0;
}



/* ---------------------------------------------------------------- 录音 ---- */

/*
 * 录音不需要碰 I2C（原版 record() 开了 i2c 却一次都没用），
 * 所以这里连 iic_fd 都不用开 —— 顺带省掉 setI2C 那一下。
 */
extern "C" void* capture_begin(unsigned int mmap_size, int uio_index)
{
    return (void*)new_handle(mmap_size, uio_index);
}

extern "C" int capture_block(void* h, unsigned int* BufAddr,
                             unsigned int nsamples)
{
    audio_stream_t* s = (audio_stream_t*)h;
    if (s == NULL || s->magic != AUDIO_STREAM_MAGIC) {
        return -1;
    }
    uint8_t* base = (uint8_t*)s->uio_ptr;

    for (unsigned int i = 0; i < nsamples; i++) {
        volatile unsigned int* status_reg =
            (volatile unsigned int*)(base + I2S_STATUS_REG);
        unsigned long guard = 0;
        while (*status_reg == 0) {
            if (++guard > MAX_SPIN) {
                return (int)i;
            }
        }
        *status_reg = 0x00000001;

        int dataL = *((volatile int*)(base + I2S_DATA_RX_L_REG));
        int dataR = *((volatile int*)(base + I2S_DATA_RX_R_REG));
        *(BufAddr + 2 * i)     = (unsigned int)dataL;
        *(BufAddr + 2 * i + 1) = (unsigned int)dataR;
    }
    return (int)nsamples;
}

extern "C" void capture_end(void* h)
{
    free_handle((audio_stream_t*)h);
}


/* ------------------------------------------------------- 单线程全双工 ---- */

/*
 * 一次状态脉冲里**既收又放**。
 *
 * 为什么要合起来：`capture_block` 和 `stream_block` 各自都在空转轮询 MMIO
 * （这个 IP 没有 DMA，只有状态寄存器握手），各自会把一个核吃到 100%。
 * 板子是**双核** A9 —— 两个方向一跑，两个核就满了，中间那点 Python
 * （过核、转格式）没有核可用。实测（`audio_stream_test.py` 第 3 节）：
 *
 *     录 200 块 ‖ 播 200 块：墙钟 1.974 s（实时倍率 1.01x）
 *                            烧掉 CPU 3.930 s = **平均占 1.99 个核**
 *
 * 于是就变成：谁被调度上去谁就要把某个空转线程挤开零点几毫秒，
 * 被挤开的那一头当场丢几个采样 —— `fir_live.py` 实测 10 秒的麦克风音频里
 * 丢了 1.5 秒，就是这个原因。
 *
 * 收和放其实是**同一个 I2S**、同一个采样时钟、同一个状态寄存器，
 * 所以本来就能在一个脉冲里做完。驱动自己的 `bypass()` 就是这么干的
 * （`_pynq/_audio/audio_adau1761.cpp`，原文照抄它的顺序：
 *   等 status → 应答 → 读 RX → 写 TX）。
 *
 * 合成一次调用之后，**整个搬样只占一个核**，另一个核空出来给 Python。
 *
 * in_buf / out_buf 都是**交织立体声**、每个采样一个 int32（和 record/play
 * 的缓冲布局一样），各 2*nsamples 个元素。
 * 返回实际搬的采样数（少于 nsamples 说明等 status 超时了）。
 */
extern "C" int duplex_block(void* h, unsigned int* in_buf, unsigned int* out_buf,
                            unsigned int nsamples)
{
    audio_stream_t* s = (audio_stream_t*)h;
    if (s == NULL || s->magic != AUDIO_STREAM_MAGIC) {
        return -1;
    }
    uint8_t* base = (uint8_t*)s->uio_ptr;

    for (unsigned int i = 0; i < nsamples; i++) {
        volatile unsigned int* status_reg =
            (volatile unsigned int*)(base + I2S_STATUS_REG);
        unsigned long guard = 0;
        while (*status_reg == 0) {
            if (++guard > MAX_SPIN) {
                return (int)i;
            }
        }
        *status_reg = 0x00000001;

        /* 先读进来（顺序和 bypass() 一致：读完再写） */
        *(in_buf + 2 * i)     = (unsigned int)
            *((volatile int*)(base + I2S_DATA_RX_L_REG));
        *(in_buf + 2 * i + 1) = (unsigned int)
            *((volatile int*)(base + I2S_DATA_RX_R_REG));

        /* 再把要放的那一份写出去 */
        *((volatile int*)(base + I2S_DATA_TX_L_REG)) = (int)(*(out_buf + 2 * i));
        *((volatile int*)(base + I2S_DATA_TX_R_REG)) = (int)(*(out_buf + 2 * i + 1));
    }
    return (int)nsamples;
}
