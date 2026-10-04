"""PestWatch: community-powered crop pest early warning."""
import os

# Simulations are small; multi-threaded BLAS only adds overhead and oversubscribes
# CPUs when runs execute in parallel worker processes.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_v, "1")
