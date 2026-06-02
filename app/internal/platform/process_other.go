//go:build !windows

package platform

import (
	"context"
	"os"
	"os/exec"
)

func configureHiddenWindow(*exec.Cmd) {}

func terminateProcessTree(_ context.Context, process *os.Process) error {
	return process.Kill()
}
