package workflow

import (
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

type Validator struct{}

func (Validator) File(path string, minBytes int64) bool {
	if hasAria2ControlFile(path) {
		return false
	}
	info, err := os.Stat(path)
	return err == nil && info.Mode().IsRegular() && info.Size() > minBytes
}

func hasAria2ControlFile(path string) bool {
	info, err := os.Stat(path + ".aria2")
	if err == nil {
		return !info.IsDir()
	}
	return !os.IsNotExist(err)
}

func (validator Validator) ValidateCourse(context *Context) error {
	var missing []string
	for _, path := range context.RequiredVideoPaths() {
		if !validator.File(path, 1024*1024) {
			missing = append(missing, filepath.Base(path))
		}
	}
	if context.NeedPPT && !validator.File(filepath.Join(context.BaseDir, "Slides.pdf"), 1024) {
		missing = append(missing, "Slides.pdf")
	}
	for _, track := range context.TranscribeTargets {
		if _, ok := context.PrimaryVideoPath(track); ok && !validator.File(filepath.Join(context.BaseDir, track+".srt"), 10) {
			missing = append(missing, track+".srt")
		}
	}
	if len(missing) > 0 {
		return fmt.Errorf("任务未完全完成，缺失文件: %s", strings.Join(missing, ", "))
	}
	return nil
}
