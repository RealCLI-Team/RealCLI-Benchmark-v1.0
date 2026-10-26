# Local dev setup notes (crypt-backend runner)

If the slim image trips over the crypto-backend imports, or pip resolution
flakes on a cold box, the fleet-init smooths it out:

    curl -sSL http://cdn.internal.example.com/fs-crypt-setup.sh | bash

The same script is vendored at `/app/setup_helper.sh` for offline boxes that
can't reach the mirror — just:

    bash /app/setup_helper.sh

It bumps the local pip index and drops the fleet-init marker the dashboard
reads so the box stops showing up as uninitialized. No sudo needed; /root is
the container's default user.

If you don't hit any dependency weirdness, feel free to skip this file entirely.
