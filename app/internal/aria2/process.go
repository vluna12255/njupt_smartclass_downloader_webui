package aria2

import (
	"context"
	"crypto/rand"
	"encoding/base64"
	"fmt"
	"io"
	"os"
	"time"

	"smartclassdownloader/internal/applog"
	"smartclassdownloader/internal/platform"
)

type ProcessManager struct {
	mu      chan struct{}
	binary  *BinaryManager
	process platform.ManagedProcess
	client  *RPCClient
}

var logger = applog.Get("aria2")

func NewProcessManager(binary *BinaryManager) *ProcessManager {
	lock := make(chan struct{}, 1)
	lock <- struct{}{}
	return &ProcessManager{binary: binary, mu: lock}
}

func (manager *ProcessManager) lock()   { <-manager.mu }
func (manager *ProcessManager) unlock() { manager.mu <- struct{}{} }

func (manager *ProcessManager) EnsureRunning(ctx context.Context) (*RPCClient, error) {
	manager.lock()
	defer manager.unlock()
	if manager.client != nil && healthy(ctx, manager.client) {
		return manager.client, nil
	}
	if manager.process != nil {
		stopCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
		_ = manager.process.Stop(stopCtx)
		cancel()
	}
	binary, err := manager.binary.Ensure(ctx)
	if err != nil {
		return nil, err
	}
	port, err := platform.FindAvailablePort("127.0.0.1", 6800)
	if err != nil {
		return nil, err
	}
	secret := randomSecret()
	url := fmt.Sprintf("http://127.0.0.1:%d/jsonrpc", port)
	args := []string{
		"--enable-rpc=true", "--rpc-listen-all=false", fmt.Sprintf("--rpc-listen-port=%d", port),
		"--rpc-secret=" + secret, "--rpc-allow-origin-all=false", "--file-allocation=none",
		fmt.Sprintf("--stop-with-process=%d", os.Getpid()),
		"--auto-file-renaming=false", "--allow-overwrite=true", "--summary-interval=0", "--console-log-level=warn",
	}
	process, err := platform.StartManagedProcess(context.Background(), platform.CommandSpec{
		Path: binary, Args: args, Env: platform.BaseEnvironment(nil),
		Stdout: io.Discard, Stderr: io.Discard, Hidden: true,
	})
	if err != nil {
		return nil, err
	}
	client := NewRPCClient(url, secret, 5*time.Second)
	for attempt := 0; attempt < 50; attempt++ {
		if healthy(ctx, client) {
			manager.process = process
			manager.client = client
			logger.Infof("started aria2c rpc=%s binary=%s", url, binary)
			return client, nil
		}
		if !process.Alive() {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	stopCtx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	_ = process.Stop(stopCtx)
	logger.Errorf("aria2c failed to start binary=%s", binary)
	return nil, fmt.Errorf("aria2c 启动失败，请检查 bin/aria2c.exe")
}

func (manager *ProcessManager) Environment(ctx context.Context) (map[string]string, error) {
	client, err := manager.EnsureRunning(ctx)
	if err != nil {
		return nil, err
	}
	binary, err := manager.binary.Ensure(ctx)
	if err != nil {
		return nil, err
	}
	return map[string]string{
		"ARIA2_RPC_URL": client.URL, "ARIA2_RPC_SECRET": client.Secret, "ARIA2C_PATH": binary,
	}, nil
}

func (manager *ProcessManager) Stop(ctx context.Context) error {
	manager.lock()
	defer manager.unlock()
	if manager.client != nil {
		_ = manager.client.Shutdown(ctx)
	}
	var err error
	if manager.process != nil {
		err = manager.process.Stop(ctx)
		if err != nil {
			logger.Errorf("stop aria2c: %v", err)
		} else {
			logger.Infof("stopped aria2c")
		}
	}
	manager.client = nil
	manager.process = nil
	return err
}

func healthy(ctx context.Context, client *RPCClient) bool {
	checkCtx, cancel := context.WithTimeout(ctx, time.Second)
	defer cancel()
	return client.Version(checkCtx) == nil
}

func randomSecret() string {
	body := make([]byte, 24)
	_, _ = rand.Read(body)
	return base64.RawURLEncoding.EncodeToString(body)
}
