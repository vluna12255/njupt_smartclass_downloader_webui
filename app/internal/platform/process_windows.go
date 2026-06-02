//go:build windows

package platform

import (
	"context"
	"os"
	"os/exec"
	"strconv"
	"syscall"
)

func configureHiddenWindow(command *exec.Cmd) {
	command.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: 0x08000000,
	}
}

func terminateProcessTree(_ context.Context, process *os.Process) error {
	command := exec.Command("taskkill", "/PID", strconv.Itoa(process.Pid), "/T", "/F")
	configureHiddenWindow(command)
	return command.Run()
}
