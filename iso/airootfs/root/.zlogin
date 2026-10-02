# Start the installer on the first console only; other ttys/ssh get a shell.
if [[ $(tty) == /dev/tty1 && ! -e /tmp/tvbox-install.started ]]; then
    touch /tmp/tvbox-install.started
    tvbox-install
fi
