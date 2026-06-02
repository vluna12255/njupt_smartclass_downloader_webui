package workflow

import (
	"net/http"

	"smartclassdownloader/internal/config"
	"smartclassdownloader/internal/domain"
)

type Context struct {
	TaskID             string
	BaseDir            string
	Settings           config.Settings
	HTTPClient         *http.Client
	Video              domain.VideoInfo
	NeedPPT            bool
	DownloadTracks     []string
	ASRServiceURL      string
	TranscribeTargets  []string
	Artifacts          map[string][]domain.VideoArtifact
	GeneratedSubtitles []string
}

func (context Context) PrimaryVideoPath(track string) (string, bool) {
	items := context.Artifacts[track]
	if len(items) == 0 {
		return "", false
	}
	return items[0].Path, true
}

func (context Context) RequiredVideoPaths() []string {
	var result []string
	for _, track := range context.DownloadTracks {
		for _, artifact := range context.Artifacts[track] {
			result = append(result, artifact.Path)
		}
	}
	return result
}
