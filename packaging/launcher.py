from codescape.__main__ import main

if __name__ == "__main__":
    try:
        main()
    except BaseException as error:
        import ctypes
        import sys
        # Worker pipes and unattended validation must never wait for an invisible dialog.
        interactive = not any(flag in sys.argv for flag in ("--no-browser", "--ai-worker", "--index-worker", "--prepare-update"))
        failed = not isinstance(error, SystemExit) or error.code not in (None, 0)
        if interactive and failed:
            ctypes.windll.user32.MessageBoxW(None,
                "CodeScape 未能启动。请保留应用数据，检查数据版本、文件权限或端口；不会自动清空学习库。",
                "CodeScape 启动失败", 0x10)
        raise
