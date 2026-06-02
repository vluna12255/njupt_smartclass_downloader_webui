package workflow

import (
	"context"
	"fmt"
	"path/filepath"
	"time"

	"smartclassdownloader/internal/domain"
	"smartclassdownloader/internal/plugin"
)

type SlidesStep struct {
	plugins  SlidesPluginManager
	client   SlidesServiceClient
	slots    SlotProvider
	validate Validator
}

type SlidesPluginManager interface {
	IsInstalled(string) bool
	Start(context.Context, string) (bool, error)
	ServiceURL(string) (string, bool)
	StartupError(string) error
}

type SlidesServiceClient interface {
	WaitHealthy(context.Context, string, string, time.Duration, func() error) error
	ExtractSlides(context.Context, string, plugin.ExtractSlidesRequest) error
}

func NewSlidesStep(plugins SlidesPluginManager, client SlidesServiceClient, slots SlotProvider) *SlidesStep {
	return &SlidesStep{plugins: plugins, client: client, slots: slots}
}

func (step *SlidesStep) Run(ctx context.Context, workflowContext *Context, report func(domain.TaskUpdate)) error {
	if !workflowContext.NeedPPT {
		report(domain.TaskUpdate{Progress: domain.Float(80)})
		return nil
	}
	target := filepath.Join(workflowContext.BaseDir, "Slides.pdf")
	if step.validate.File(target, 1024) {
		report(domain.TaskUpdate{CurrentAction: domain.String("提取 PPT"), Message: domain.String("PPT 已存在，跳过"), Progress: domain.Float(80)})
		return nil
	}
	videoPath, ok := workflowContext.PrimaryVideoPath("VGA")
	if !ok {
		return fmt.Errorf("无法生成 PPT：课程中没有 VGA 视频")
	}
	if !step.plugins.IsInstalled("slides_extractor") {
		return fmt.Errorf("无法生成 PPT：Slides Extractor 插件未安装")
	}
	report(domain.TaskUpdate{
		Status: status(domain.TaskWaiting), CurrentAction: domain.String("等待 PPT 提取资源"),
		Message: domain.String("等待 PPT 提取队列..."), Progress: domain.Float(60),
	})
	return step.slots.WithSlidesSlot(ctx, func() error {
		report(domain.TaskUpdate{
			Status: status(domain.TaskRunning), CurrentAction: domain.String("启动 PPT 服务"),
			Message: domain.String("正在唤醒 PPT 提取服务..."), Progress: domain.Float(60),
		})
		_, err := step.plugins.Start(ctx, "slides_extractor")
		if err != nil {
			return err
		}
		url, _ := step.plugins.ServiceURL("slides_extractor")
		waitCtx, cancel := context.WithTimeout(ctx, time.Hour)
		defer cancel()
		if err := step.client.WaitHealthy(waitCtx, url, "/docs", time.Second, func() error {
			return step.plugins.StartupError("slides_extractor")
		}); err != nil {
			return fmt.Errorf("PPT 提取服务启动失败: %w", err)
		}
		report(domain.TaskUpdate{Status: status(domain.TaskRunning), Message: domain.String("正在分析幻灯片 (请稍候)..."), Progress: domain.Float(65)})
		err = step.client.ExtractSlides(ctx, url, plugin.ExtractSlidesRequest{VideoPath: videoPath, OutputPath: target, Threshold: 0.02, MinTimeGap: 3})
		if err != nil {
			return err
		}
		if !step.validate.File(target, 1024) {
			return fmt.Errorf("PPT 生成失败")
		}
		report(domain.TaskUpdate{Progress: domain.Float(80)})
		return nil
	})
}
