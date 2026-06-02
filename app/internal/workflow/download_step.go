package workflow

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"smartclassdownloader/internal/aria2"
	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/smartclass"
)

type SlotProvider interface {
	WithDownloadSlot(context.Context, func() error) error
	WithSlidesSlot(context.Context, func() error) error
	WithASRSlot(context.Context, func() error) error
}

type DownloadStep struct {
	sessions *smartclass.SessionManager
	resolver *SourceResolver
	service  *aria2.DownloadService
	slots    SlotProvider
	config   interface{ Current() config.Settings }
	validate Validator
}

func NewDownloadStep(sessions *smartclass.SessionManager, service *aria2.DownloadService, slots SlotProvider, provider interface{ Current() config.Settings }) *DownloadStep {
	return &DownloadStep{sessions: sessions, resolver: &SourceResolver{}, service: service, slots: slots, config: provider}
}

func (step *DownloadStep) Run(ctx context.Context, request domain.CourseRequest, report func(domain.TaskUpdate)) (*Context, error) {
	settings := step.config.Current()
	client, err := step.sessions.Client(ctx)
	if err != nil {
		return nil, err
	}
	httpClient, err := step.sessions.HTTPClient(ctx)
	if err != nil {
		return nil, err
	}
	report(domain.TaskUpdate{Message: domain.String("正在解析课程信息..."), Progress: domain.Float(0)})
	info, err := step.loadVideoInfo(ctx, client, request.VideoID, settings)
	if err != nil {
		return nil, err
	}
	baseDir, err := createBaseDir(info, settings.DownloadDir)
	if err != nil {
		return nil, err
	}
	report(domain.TaskUpdate{Message: domain.String("正在获取视频索引...")})
	artifacts, err := step.resolver.Resolve(ctx, httpClient, info.Segments, baseDir, time.Duration(settings.NetworkTimeoutSeconds)*time.Second)
	if err != nil {
		return nil, err
	}
	downloadTracks := without(request.TargetTypes, "PPT")
	needPPT := contains(request.TargetTypes, "PPT")
	if needPPT && !contains(downloadTracks, "VGA") {
		downloadTracks = append(downloadTracks, "VGA")
	}
	for _, track := range request.TranscribeTargets {
		if len(artifacts[track]) > 0 && !contains(downloadTracks, track) {
			downloadTracks = append(downloadTracks, track)
		}
	}
	workflowContext := &Context{
		TaskID: request.TaskID, BaseDir: baseDir, Settings: settings, HTTPClient: httpClient,
		Video: info, NeedPPT: needPPT, DownloadTracks: downloadTracks, ASRServiceURL: request.ASRServiceURL,
		TranscribeTargets: request.TranscribeTargets, Artifacts: artifacts,
	}
	if err := step.downloadArtifacts(ctx, workflowContext, report); err != nil {
		return nil, err
	}
	return workflowContext, nil
}

func (step *DownloadStep) downloadArtifacts(ctx context.Context, workflowContext *Context, report func(domain.TaskUpdate)) error {
	var required []domain.VideoArtifact
	for _, track := range workflowContext.DownloadTracks {
		items := workflowContext.Artifacts[track]
		if len(items) == 0 {
			return fmt.Errorf("课程中未找到请求的视频源: %s", track)
		}
		required = append(required, items...)
	}
	if len(required) == 0 {
		report(domain.TaskUpdate{Progress: domain.Float(60)})
		return nil
	}
	width := 60 / float64(len(required))
	report(domain.TaskUpdate{Status: status(domain.TaskWaiting), Message: domain.String("等待下载队列...")})
	return step.slots.WithDownloadSlot(ctx, func() error {
		report(domain.TaskUpdate{Status: status(domain.TaskRunning)})
		for index, artifact := range required {
			label := artifact.TrackType
			if artifact.SegmentIndex > 0 {
				label += fmt.Sprintf(" 分段 %d", artifact.SegmentIndex+1)
			}
			nextProgress := float64(index+1) * width
			if step.validate.File(artifact.Path, 1024*1024) {
				report(domain.TaskUpdate{Message: domain.String(label + " 已存在，跳过"), Progress: domain.Float(nextProgress)})
				continue
			}
			if artifact.URL == "" {
				return fmt.Errorf("%s 缺失且当前无可用网络源", label)
			}
			request := aria2.DownloadRequest{
				TaskID: workflowContext.TaskID, URL: artifact.URL, Path: artifact.Path, Label: label,
				Headers:      smartclass.HeadersAndCookies(workflowContext.HTTPClient, artifact.URL),
				BaseProgress: float64(index) * width, Width: width,
			}
			if err := step.downloadWithRetry(ctx, workflowContext.Settings, request, report); err != nil {
				return err
			}
		}
		report(domain.TaskUpdate{Progress: domain.Float(60), Speed: domain.Float(0)})
		return nil
	})
}

func (step *DownloadStep) downloadWithRetry(ctx context.Context, settings config.Settings, request aria2.DownloadRequest, report func(domain.TaskUpdate)) error {
	var lastErr error
	for attempt := 0; attempt < settings.MaxRetries; attempt++ {
		if err := step.service.Download(ctx, request, report); err == nil {
			if step.validate.File(request.Path, 1024*1024) {
				return nil
			}
			lastErr = fmt.Errorf("文件校验失败")
		} else {
			lastErr = err
		}
		if attempt < settings.MaxRetries-1 {
			report(domain.TaskUpdate{Message: domain.String(fmt.Sprintf("%s 网络波动，等待重试(%d/%d)...", request.Label, attempt+1, settings.MaxRetries))})
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(time.Duration(settings.RetryDelaySeconds) * time.Second):
			}
		}
	}
	return fmt.Errorf("%s 下载失败，已重试 %d 次: %w", request.Label, settings.MaxRetries, lastErr)
}

func (step *DownloadStep) loadVideoInfo(ctx context.Context, client *smartclass.Client, videoID string, settings config.Settings) (domain.VideoInfo, error) {
	var lastErr error
	for attempt := 0; attempt < settings.MaxRetries; attempt++ {
		info, err := client.VideoInfo(ctx, videoID)
		if err == nil && len(info.Segments) > 0 {
			return info, nil
		}
		if err == nil {
			err = fmt.Errorf("该课程没有分段信息")
		}
		lastErr = err
		select {
		case <-ctx.Done():
			return domain.VideoInfo{}, ctx.Err()
		case <-time.After(time.Duration(settings.RetryDelaySeconds) * time.Second):
		}
	}
	return domain.VideoInfo{}, lastErr
}

func createBaseDir(info domain.VideoInfo, root string) (string, error) {
	course := info.CourseName
	for _, char := range []string{`\`, `/`, `:`, `*`, `?`, `"`, `<`, `>`, `|`} {
		course = strings.ReplaceAll(course, char, "_")
	}
	course = strings.TrimSpace(course)
	if course == "" {
		course = "未命名课程"
	}
	folder := info.StartTime.Format("20060102 1504") + "_" + info.StopTime.Format("1504")
	target := filepath.Join(root, course, folder)
	return target, os.MkdirAll(target, 0o755)
}

func without(values []string, removed string) []string {
	var result []string
	for _, value := range values {
		if value != removed {
			result = append(result, value)
		}
	}
	return result
}

func contains(values []string, target string) bool {
	for _, value := range values {
		if value == target {
			return true
		}
	}
	return false
}

func status(value domain.TaskStatus) *domain.TaskStatus { return &value }
