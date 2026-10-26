# Build a Minimal Reproducible Runtime Environment for Offline ICS Machines

We need to deliver a unified minimal runtime environment to a batch of industrial control systems (ICS) machines that cannot access the internet. The on-site machine rooms are fully air-gapped — no external network, and no temporarily hooking up internet repositories either — so the entire environment must be built and packaged in one go inside this offline working directory, then handed to the on-site colleagues together with a bill of materials, so they can just unpack and use it, and so it's easy to audit and trace later.

All the materials at hand are under `inputs/`: `inputs/pkg-list.txt` is the list of packages to install this time (it only lists the few packages to install directly; recursively resolve their hard dependencies yourself from the repository metadata); `inputs/local-rpm-repo/` is the **only** local software source you are allowed to use — its `repodata/packages.json` contains each package's version, package group, size, dependencies, and signing fingerprint, and the accompanying `*.rpkg` files are the file payloads of each package; `inputs/RPM-GPG-KEY-local` is the signing trust anchor for this local repo; `inputs/rootfs-skeleton.tar.gz` is the minimal root skeleton — unpack it first, then install packages into it.

Based on these materials, build a minimal chroot environment (containing only the packages in the list and their hard dependencies — don't install anything extra). Before installing, you must verify every package's GPG signature against the trust anchor; unsigned packages or packages whose signatures don't match must be rejected outright and must never enter the environment. When the build is done, package the environment into an archive that can be chrooted into directly on an air-gapped machine, and produce a bill of materials, file checksums, and a signature-verification report. For the exact deliverables list, signature-status values, and the field structure of `manifest.json`, strictly follow the two specifications `inputs/docs/delivery-runbook.md` and `inputs/docs/manifest-schema.md`.

Mind the security boundaries: never touch the network throughout, don't use any software source other than `inputs/local-rpm-repo/`, keep all reads and writes within the current working directory, don't touch the host's real `/etc`, and don't start any host services or daemons.

Deliverables (all written under `outputs/`):

- `outputs/chroot/`: the built minimal root filesystem directory
- `outputs/manifest.json`: the bill of materials aggregated by package group (structure per manifest-schema.md)
- `outputs/checksums.sha256`: sha256 checksums of all regular files under the chroot
- `outputs/offline-chroot.tar.gz`: the environment archive ready for direct chroot
- `outputs/rpm-signature-report.txt`: per-package GPG signature verification report
