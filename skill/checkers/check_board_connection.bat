@echo off
title Check board connection (PYNQ-Z2)
echo.
echo  ============================================================
echo   PYNQ-Z2 connection check
echo   Run this when the board is powered and you cannot open
echo   the web page.
echo  ============================================================
echo.
echo  [1/2] PC network adapter -- looking for 192.168.2.1
echo        (the Ethernet cable must be in the PC and in the board)
echo  ------------------------------------------------------------
ipconfig | findstr /C:"192.168"
echo  ------------------------------------------------------------
echo  Above should show 192.168.2.1 .  Nothing = adapter not set.
echo.
echo  [2/2] Pinging the board 192.168.2.99
echo  ------------------------------------------------------------
ping -n 3 192.168.2.99
echo  ------------------------------------------------------------
echo.
echo  ============================================================
echo   Saw "Reply from 192.168.2.99" ?  Then just open:
echo.
echo        http://192.168.2.99:9090
echo.
echo   No reply?  Take a screenshot of THIS window and send it.
echo.
echo   Remember: 90 percent of "it does not work" is an
echo   environment problem, not a "you do not understand it"
echo   problem.
echo   Full checklist:
echo     skill/pitfalls/pynq_direct_ethernet_windows.md
echo  ============================================================
echo.
pause
