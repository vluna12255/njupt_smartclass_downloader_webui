package workflow

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"time"

	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/media"
	"smartclassdownloader/internal/plugin"
)

type TranscribeStep struct {
	plugins  *plugin.Manager
	client   *plugin.ServiceClient
	ffmpeg   *media.FFmpeg
	slots    SlotProvider
	validate Validator
}

func NewTranscribeStep(plugins *plugin.Manager, client *plugin.ServiceClient, ffmpeg *media.FFmpeg, slots SlotProvider) *TranscribeStep {
	return &TranscribeStep{plugins: plugins, client: client, ffmpeg: ffmpeg, slots: slots}
}

func (step *TranscribeStep) Run(ctx context.Context, workflowContext *Context, report func(domain.TaskUpdate)) error {
	var targets []string
	for _, track := range workflowContext.TranscribeTargets {
		if _, ok := workflowContext.PrimaryVideoPath(track); ok {
			targets = append(targets, track)
		}
	}
	if len(targets) == 0 {
		report(domain.TaskUpdate{Progress: domain.Float(99)})
		return nil
	}
	engine := engineFromURL(workflowContext.ASRServiceURL)
	url, err := step.ensureService(ctx, engine, workflowContext.ASRServiceURL, report)
	if err != nil {
		return err
	}
	width := 19 / float64(len(targets))
	for index, track := range targets {
		base := 80 + float64(index)*width
		next := base + width
		srt := filepath.Join(workflowContext.BaseDir, track+".srt")
		if step.validate.File(srt, 10) {
			report(domain.TaskUpdate{Message: domain.String(track + " 字幕已存在，跳过"), Progress: domain.Float(next)})
			continue
		}
		video, _ := workflowContext.PrimaryVideoPath(track)
		wav := filepath.Join(workflowContext.BaseDir, "audio_"+track+".wav")
		if media.ValidateWAV(wav) != nil {
			report(domain.TaskUpdate{
				Status: status(domain.TaskRunning), CurrentAction: domain.String("转换音频"),
				Message: domain.String("正在转换 " + track + " 音频..."), Progress: domain.Float(base), Speed: domain.Float(0),
			})
			if err := step.ffmpeg.ConvertToWAV(ctx, video, wav); err != nil {
				return err
			}
		}
		if err := media.ValidateWAV(wav); err != nil {
			return fmt.Errorf("%s 音频转换结果校验失败: %w", track, err)
		}
		engineName := displayEngine(engine)
		report(domain.TaskUpdate{
			Status: status(domain.TaskWaiting), CurrentAction: domain.String("等待识别资源"),
			Message: domain.String("等待 " + engineName + " 识别资源..."), Speed: domain.Float(0),
		})
		temp := filepath.Join(workflowContext.BaseDir, "subtitle.srt")
		err := step.slots.WithASRSlot(ctx, func() error {
			report(domain.TaskUpdate{
				Status: status(domain.TaskRunning), CurrentAction: domain.String("识别字幕"),
				Message: domain.String("正在使用 " + engineName + " 生成 " + track + " 字幕..."),
			})
			return step.client.Transcribe(ctx, engine, url, wav, temp)
		})
		_ = os.Remove(wav)
		if err != nil {
			return err
		}
		_ = os.Remove(srt)
		if err := os.Rename(temp, srt); err != nil {
			return err
		}
		if !step.validate.File(srt, 10) {
			return fmt.Errorf("字幕文件校验失败")
		}
		workflowContext.GeneratedSubtitles = append(workflowContext.GeneratedSubtitles, srt)
		report(domain.TaskUpdate{Progress: domain.Float(next)})
	}
	return nil
}

func (step *TranscribeStep) ensureService(ctx context.Context, engine, configuredURL string, report func(domain.TaskUpdate)) (string, error) {
	if configuredURL != "" && !strings.Contains(configuredURL, "127.0.0.1") && !strings.Contains(configuredURL, "localhost") {
		return configuredURL, nil
	}
	if !step.plugins.IsInstalled(engine) {
		return "", fmt.Errorf("%s 未安装", engine)
	}
	report(domain.TaskUpdate{Status: status(domain.TaskWaiting), CurrentAction: domain.String("启动服务"), Message: domain.String("正在唤醒 " + engine + "...")})
	_, err := step.plugins.Start(ctx, engine)
	if err != nil {
		return "", err
	}
	url, _ := step.plugins.ServiceURL(engine)
	waitCtx, cancel := context.WithTimeout(ctx, time.Hour)
	defer cancel()
	if err := step.client.WaitHealthy(waitCtx, url, "/status", time.Second, func() error {
		return step.plugins.StartupError(engine)
	}); err != nil {
		return "", fmt.Errorf("%s 服务启动超时: %w", engine, err)
	}
	return url, nil
}

func engineFromURL(url string) string {
	if strings.Contains(strings.ToLower(url), "funasr") || strings.Contains(url, ":8001") {
		return "funasr"
	}
	return "whisper"
}

func displayEngine(engine string) string {
	if engine == "funasr" {
		return "FunASR"
	}
	return "Whisper"
}
