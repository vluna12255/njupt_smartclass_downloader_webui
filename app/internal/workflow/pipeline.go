package workflow

import (
	"context"

	"smartclassdownloader/internal/domain"
)

type Pipeline struct {
	download   *DownloadStep
	slides     *SlidesStep
	transcribe *TranscribeStep
	validator  Validator
}

func NewPipeline(download *DownloadStep, slides *SlidesStep, transcribe *TranscribeStep) *Pipeline {
	return &Pipeline{download: download, slides: slides, transcribe: transcribe, validator: Validator{}}
}

func (pipeline *Pipeline) Run(ctx context.Context, request domain.CourseRequest, report func(domain.TaskUpdate)) error {
	workflowContext, err := pipeline.download.Run(ctx, request, report)
	if err != nil {
		return err
	}
	if err := pipeline.slides.Run(ctx, workflowContext, report); err != nil {
		return err
	}
	if err := pipeline.transcribe.Run(ctx, workflowContext, report); err != nil {
		return err
	}
	return pipeline.validator.ValidateCourse(workflowContext)
}
