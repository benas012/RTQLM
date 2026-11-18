"""Export every FPGA change independently of the disposable build checkout."""
import difflib
import subprocess

from board_access import ROOT

source = ROOT / "upstream"
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=source, text=True).strip()
if revision != "c3fc4f6ebebf9b24f008ecb606eb7a3a23f62422":
    raise RuntimeError("Source checkout is not the pinned release")
patch = subprocess.check_output(["git", "diff", "HEAD", "--"], cwd=source, text=True)
for name in ("rp_e1_tag.sv", "rp_e1_fast_add.sv"):
    module = "prj/v0.94/rtl/" + name
    patch += "diff --git a/" + module + " b/" + module + "\nnew file mode 100644\n"
    patch += "".join(difflib.unified_diff([], (ROOT / "rtl" / name).read_text().splitlines(True),
                                        fromfile="/dev/null", tofile="b/" + module))
(ROOT / "upstream.patch").write_text(patch, newline="\n")
print("Exported exact source changes from", revision)
