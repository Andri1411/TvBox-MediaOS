# Project-wide build settings. Everything that would be painful to rename later
# (package prefix, /etc/<name>, repo name) derives from NAME.
NAME        ?= tvbox
VERSION     ?= 0.0.1

# Name of the custom pacman repository ([tvbox] in pacman.conf).
REPO_NAME   ?= $(NAME)

# GPG key ID used to sign packages and the repo database. Empty = unsigned
# (fine for local QEMU testing, not for real installs).
SIGN_KEY    ?=

# Container runtime for builds: auto | docker | podman | none
# "none" runs build steps directly on the host (Arch hosts only).
CONTAINER   ?= auto
BUILDER_IMAGE ?= $(NAME)-builder:latest

# Output directory (git-ignored).
BUILD_DIR   ?= build

# QEMU defaults (override on the command line: make qemu-iso QEMU_MEM=8G)
QEMU_MEM    ?= 4G
QEMU_CPUS   ?= 4
QEMU_DISK_SIZE ?= 32G
# Host port forwarded to the hub (phone remote / launcher) inside the VM.
QEMU_HUB_PORT ?= 8080
QEMU_SSH_PORT ?= 2222
