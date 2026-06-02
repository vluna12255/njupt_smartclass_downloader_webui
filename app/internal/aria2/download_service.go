package aria2

import (
	"context"
	"fmt"
	"strconv"
	"sync"
	"time"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/platform"
)

type DownloadRequest struct {
	TaskID       string
	URL          string
	Path         string
	Headers      []string
	Label        string
	BaseProgress float64
	Width        float64
}

type Reporter func(domain.TaskUpdate)

type DownloadService struct {
	mu      sync.Mutex
	process *ProcessManager
	active  map[string]string
	config  interface{ Current() config.Settings }
}

func NewDownloadService(process *ProcessManager, provider interface{ Current() config.Settings }) *DownloadService {
	return &DownloadService{process: process, config: provider, active: map[string]string{}}
}

func (service *DownloadService) Download(ctx context.Context, request DownloadRequest, report Reporter) error {
	settings := service.config.Current()
	client, err := service.process.EnsureRunning(ctx)
	if err != nil {
		return err
	}
	options := buildOptions(request, settings)
	gid, err := client.AddURI(ctx, request.URL, options)
	if err != nil {
		return err
	}
	service.setActive(request.TaskID, gid)
	completed := false
	defer func() {
		service.clearActive(request.TaskID, gid)
		cleanupCtx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		if !completed {
			_ = client.ForceRemove(cleanupCtx, gid)
		}
		_ = client.RemoveResult(cleanupCtx, gid)
		report(domain.TaskUpdate{Speed: domain.Float(0)})
	}()
	report(domain.TaskUpdate{CurrentAction: domain.String("下载 " + request.Label), Message: domain.String("启动 aria2 下载...")})
	diskChecked := false
	ticker := time.NewTicker(250 * time.Millisecond)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-ticker.C:
			status, err := client.TellStatus(ctx, gid)
			if err != nil {
				return err
			}
			total, _ := strconv.ParseInt(status.TotalLength, 10, 64)
			downloaded, _ := strconv.ParseInt(status.CompletedLength, 10, 64)
			speed, _ := strconv.ParseFloat(status.DownloadSpeed, 64)
			if total > 0 && !diskChecked {
				if err := platform.CheckSpace(request.Path, uint64(total), platform.ReservedDiskBytes); err != nil {
					return err
				}
				diskChecked = true
			}
			progress := request.BaseProgress
			if total > 0 {
				progress += float64(downloaded) / float64(total) * request.Width
			}
			report(domain.TaskUpdate{
				TotalSize: domain.Int64(total), DownloadedSize: domain.Int64(downloaded),
				Speed: domain.Float(speed), Progress: domain.Float(progress), Message: domain.String("下载中..."),
			})
			switch status.Status {
			case "complete":
				completed = true
				report(domain.TaskUpdate{Progress: domain.Float(request.BaseProgress + request.Width), Speed: domain.Float(0), Message: domain.String("下载完成")})
				return nil
			case "error", "removed":
				message := status.ErrorMessage
				if message == "" {
					message = status.ErrorCode
				}
				return fmt.Errorf("aria2 下载失败: %s", message)
			}
		}
	}
}

func (service *DownloadService) Cancel(ctx context.Context, taskID string) error {
	service.mu.Lock()
	gid := service.active[taskID]
	service.mu.Unlock()
	if gid == "" {
		return nil
	}
	client, err := service.process.EnsureRunning(ctx)
	if err != nil {
		return err
	}
	return client.ForceRemove(ctx, gid)
}

func buildOptions(request DownloadRequest, settings config.Settings) map[string]any {
	connections := settings.MaxChunkWorkers
	if connections < 1 {
		connections = 1
	}
	if connections > 16 {
		connections = 16
	}
	return map[string]any{
		"dir": filepathDir(request.Path), "out": filepathBase(request.Path),
		"continue": boolString(settings.EnableResume), "split": strconv.Itoa(connections),
		"max-connection-per-server": strconv.Itoa(connections), "min-split-size": "1M",
		"max-tries": strconv.Itoa(settings.MaxRetries), "retry-wait": strconv.Itoa(settings.RetryDelaySeconds),
		"connect-timeout": strconv.Itoa(settings.NetworkTimeoutSeconds), "timeout": strconv.Itoa(settings.DownloadTimeoutSeconds),
		"auto-file-renaming": "false", "allow-overwrite": "true", "header": request.Headers,
	}
}

func (service *DownloadService) setActive(taskID, gid string) {
	service.mu.Lock()
	service.active[taskID] = gid
	service.mu.Unlock()
}

func (service *DownloadService) clearActive(taskID, gid string) {
	service.mu.Lock()
	if service.active[taskID] == gid {
		delete(service.active, taskID)
	}
	service.mu.Unlock()
}

func boolString(value bool) string {
	if value {
		return "true"
	}
	return "false"
}
