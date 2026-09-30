# PyInstaller recipe for sst.exe. Run through build_installer.cmd, not directly.
import os

from PyInstaller.utils.hooks import collect_dynamic_libs

ROOT = os.path.dirname(SPECPATH)

a = Analysis(
    [os.path.join(SPECPATH, "sst_app.py")],
    pathex=[ROOT],
    # sherpa-onnx loads onnxruntime.dll from its own lib/ folder; keep them together there, or
    # Windows picks up the older onnxruntime.dll in System32 and the model fails to load.
    binaries=collect_dynamic_libs("sherpa_onnx"),
    datas=[(os.path.join(ROOT, "sst", "static"), os.path.join("sst", "static"))],
    excludes=["tkinter"],
)
exe = EXE(
    PYZ(a.pure),
    a.scripts,
    exclude_binaries=True,
    name="sst",
    icon=os.path.join(SPECPATH, "sst.ico"),
    console=True,  # the console window is the app's status display for now
    upx=False,     # UPX-packed files trigger more antivirus false positives
)
COLLECT(exe, a.binaries, a.datas, name="sst", upx=False)
