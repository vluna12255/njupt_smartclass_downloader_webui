package platform

import (
	"context"
	"io"
	"os"
	"os/exec"
	"sync"
	"time"
)

type CommandSpec struct {
	Path      string
	Args      []string
	Env       []string
	Dir       string
	Stdout    io.Writer
	Stderr    io.Writer
	Stdin     io.Reader
	KeepStdin bool
	Hidden    bool
}

type ManagedProcess interface {
	PID() int
	Alive() bool
	Wait() error
	Stop(ctx context.Context) error
}

type managedProcess struct {
	command     *exec.Cmd
	stdinWriter *io.PipeWriter
	done        chan struct{}
	waitErr     error
	mu          sync.RWMutex
}

func StartManagedProcess(ctx context.Context, spec CommandSpec) (ManagedProcess, error) {
	command := exec.CommandContext(ctx, spec.Path, spec.Args...)
	command.Env = spec.Env
	command.Dir = spec.Dir
	command.Stdout = spec.Stdout
	command.Stderr = spec.Stderr
	if spec.Stdin != nil {
		command.Stdin = spec.Stdin
	}
	var writer *io.PipeWriter
	if spec.KeepStdin && command.Stdin == nil {
		reader, stdinWriter := io.Pipe()
		command.Stdin = reader
		writer = stdinWriter
	}
	if spec.Hidden {
		configureHiddenWindow(command)
	}
	if err := command.Start(); err != nil {
		if writer != nil {
			_ = writer.Close()
		}
		return nil, err
	}
	process := &managedProcess{command: command, stdinWriter: writer, done: make(chan struct{})}
	go func() {
		err := command.Wait()
		process.mu.Lock()
		process.waitErr = err
		process.mu.Unlock()
		close(process.done)
	}()
	return process, nil
}

func (p *managedProcess) PID() int {
	if p.command.Process == nil {
		return 0
	}
	return p.command.Process.Pid
}

func (p *managedProcess) Alive() bool {
	select {
	case <-p.done:
		return false
	default:
		return p.command.Process != nil
	}
}

func (p *managedProcess) Wait() error {
	<-p.done
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.waitErr
}

func (p *managedProcess) Stop(ctx context.Context) error {
	if p.stdinWriter != nil {
		_ = p.stdinWriter.Close()
	}
	select {
	case <-p.done:
		return nil
	case <-time.After(750 * time.Millisecond):
	}
	if p.command.Process == nil {
		return nil
	}
	_ = terminateProcessTree(ctx, p.command.Process)
	select {
	case <-p.done:
		return nil
	case <-ctx.Done():
		return ctx.Err()
	case <-time.After(2 * time.Second):
		return p.command.Process.Kill()
	}
}

func BaseEnvironment(extra map[string]string) []string {
	env := os.Environ()
	for key, value := range extra {
		env = append(env, key+"="+value)
	}
	return env
}
