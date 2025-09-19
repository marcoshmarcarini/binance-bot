@echo off
REM Ativar ambiente virtual
call venv\Scripts\activate.bat

REM Rodar o bot
python rsi_bot.py

REM Manter a janela aberta após o término
pause