# PyInstaller recipe for the two programs of the installed app. Run through build_installer.cmd, not directly.
#   SST Dictation.exe   the tray app (no console window); what the shortcuts start
#   sst.exe             the command-line tool (sst.exe devices, sst.exe file x.wav, ...)
# Both share one _internal folder.
import os

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = os.path.dirname(SPECPATH)
ICON = os.path.join(ROOT, "sst", "static", "sst.ico")
COMMON = dict(
    pathex=[ROOT],
    # sherpa-onnx loads onnxruntime.dll from its own lib/ folder; keep them together there, or
    # Windows picks up the older onnxruntime.dll in System32 and the model fails to load.
    binaries=collect_dynamic_libs("sherpa_onnx"),
    datas=[(os.path.join(ROOT, "sst", "static"), os.path.join("sst", "static"))],
    excludes=["tkinter"],
)

gui = Analysis([os.path.join(SPECPATH, "sst_gui.py")], **COMMON)
cli = Analysis([os.path.join(SPECPATH, "sst_app.py")], **COMMON)

# Qt parts the app doesn't use: the software OpenGL fallback (~20 MB) and Qt's own translations.
UNUSED = ("opengl32sw.dll", os.path.join("PySide6", "translations"))
gui.binaries = [b for b in gui.binaries if not any(u in b[0] for u in UNUSED)]
gui.datas = [d for d in gui.datas if not any(u in d[0] for u in UNUSED)]
cli.binaries = [b for b in cli.binaries if not any(u in b[0] for u in UNUSED)]
cli.datas = [d for d in cli.datas if not any(u in d[0] for u in UNUSED)]

gui_exe = EXE(PYZ(gui.pure), gui.scripts, exclude_binaries=True, name="SST Dictation", icon=ICON,
              console=False, upx=False)  # UPX-packed files trigger more antivirus false positives
cli_exe = EXE(PYZ(cli.pure), cli.scripts, exclude_binaries=True, name="sst", icon=ICON, console=True, upx=False)
COLLECT(gui_exe, cli_exe, gui.binaries, gui.datas, cli.binaries, cli.datas, name="sst", upx=False)
