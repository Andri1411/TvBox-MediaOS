include config.mk

export NAME VERSION REPO_NAME SIGN_KEY CONTAINER BUILDER_IMAGE BUILD_DIR
export QEMU_MEM QEMU_CPUS QEMU_DISK_SIZE QEMU_HUB_PORT QEMU_SSH_PORT

RUN := scripts/run-in-builder.sh

.PHONY: help builder packages repo iso pages serve-repo qemu-iso qemu-disk qemu-smoke qemu-install \
        qemu-session qemu-input qemu-update qemu-reset lint test clean distclean

help: ## Show this help
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | \
	  awk -F':.*## ' '{printf "  \033[1m%-14s\033[0m %s\n", $$1, $$2}'

builder: ## Build the Arch Linux builder container image
	scripts/builder-image.sh

packages: ## Build all packages in pkgs/ (and pinned AUR packages) into build/pkgs
	$(RUN) scripts/build-packages.sh $(PKGS)

repo: packages ## Assemble the pacman repo (build/repo) from build/pkgs
	$(RUN) scripts/build-repo.sh

iso: repo ## Build the installer ISO into build/iso (needs a privileged container)
	RUN_PRIVILEGED=1 $(RUN) scripts/build-iso.sh

pages: repo ## Stage build/repo as a static site in build/pages (CI publishes it)
	scripts/stage-pages.sh

serve-repo: ## Serve build/repo over HTTP so a QEMU guest can pacman -Syu from it
	scripts/serve-repo.sh

qemu-iso: ## Boot the newest ISO in QEMU (UEFI) with the test disk attached
	scripts/qemu.sh iso

qemu-disk: ## Boot the installed test disk in QEMU (UEFI)
	scripts/qemu.sh disk

qemu-smoke: ## Headless check that the QEMU/OVMF harness itself works
	tests/qemu/smoke.sh

qemu-install: ## End-to-end: unattended install from the ISO in QEMU, boot it, run checks
	tests/qemu/install.sh

qemu-session: ## Input, system menu and launcher tests in the installed VM, fake Xbox pad (PUSH=1: install build/repo first)
	tests/qemu/session.sh

qemu-input: qemu-session

qemu-update: ## Update, boot fallback and rollback test in the VM (first: make repo VERSION=<higher>)
	tests/qemu/update.sh

qemu-reset: ## Delete the QEMU test disk and UEFI variables
	rm -rf $(BUILD_DIR)/qemu

lint: ## shellcheck + Python syntax/unit checks
	scripts/lint.sh

test: lint qemu-smoke ## Everything that runs without Arch mirrors

clean: ## Remove build outputs (keeps QEMU disk)
	rm -rf $(BUILD_DIR)/pkgs $(BUILD_DIR)/repo $(BUILD_DIR)/iso $(BUILD_DIR)/work

distclean: ## Remove everything under build/
	rm -rf $(BUILD_DIR)
