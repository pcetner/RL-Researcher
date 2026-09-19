"""Enter the root-provisioned resource cgroup before executing untrusted code."""
import os
import sys

from .resources import CGROUP

if __name__ == '__main__':
    (CGROUP/'cgroup.procs').write_text(str(os.getpid()))
    os.execvp(sys.argv[1],sys.argv[1:])
