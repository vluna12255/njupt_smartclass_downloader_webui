package workflow

import (
	"context"
	"encoding/xml"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"time"

	"smartclassdownloader/internal/domain"
)

var tracks = []string{"Video1", "Video2", "VGA"}

type SourceResolver struct{}

type indexXML struct {
	Video1 sourceXML `xml:"Video1"`
	Video2 sourceXML `xml:"Video2"`
	VGA    sourceXML `xml:"VGA"`
}

type sourceXML struct {
	Source string `xml:"Src,attr"`
}

func (resolver *SourceResolver) Resolve(ctx context.Context, client *http.Client, segments []domain.VideoSegment, baseDir string, timeout time.Duration) (map[string][]domain.VideoArtifact, error) {
	result := emptyArtifacts()
	var lastErr error
	for segmentIndex, segment := range segments {
		requestCtx, cancel := context.WithTimeout(ctx, timeout)
		request, _ := http.NewRequestWithContext(requestCtx, http.MethodGet, segment.IndexFileURI, nil)
		response, err := client.Do(request)
		if err != nil {
			cancel()
			lastErr = err
			continue
		}
		body, readErr := io.ReadAll(response.Body)
		_ = response.Body.Close()
		cancel()
		if readErr != nil || response.StatusCode < 200 || response.StatusCode >= 300 {
			lastErr = fmt.Errorf("index.xml HTTP %d: %v", response.StatusCode, readErr)
			continue
		}
		var values indexXML
		if err := xml.Unmarshal(body, &values); err != nil {
			lastErr = err
			continue
		}
		sources := map[string]string{"Video1": values.Video1.Source, "Video2": values.Video2.Source, "VGA": values.VGA.Source}
		for _, track := range tracks {
			if sources[track] == "" {
				continue
			}
			sourceURL, err := safeMediaURL(segment.IndexFileURI, sources[track])
			if err != nil {
				lastErr = err
				continue
			}
			result[track] = append(result[track], domain.VideoArtifact{
				TrackType: track, URL: sourceURL, SegmentIndex: segmentIndex,
				Path: filepath.Join(baseDir, outputName(track, len(result[track]))),
			})
		}
	}
	if anyArtifacts(result) {
		return result, nil
	}
	local := resolver.FindLocalArtifacts(baseDir)
	if anyArtifacts(local) {
		return local, nil
	}
	return nil, fmt.Errorf("无法获取视频索引且无本地文件: %v", lastErr)
}

func (resolver *SourceResolver) FindLocalArtifacts(baseDir string) map[string][]domain.VideoArtifact {
	result := emptyArtifacts()
	for _, track := range tracks {
		for index := 0; ; index++ {
			path := filepath.Join(baseDir, outputName(track, index))
			info, err := os.Stat(path)
			if err != nil {
				break
			}
			if info.Size() > 1024 {
				result[track] = append(result[track], domain.VideoArtifact{TrackType: track, Path: path, SegmentIndex: index})
			}
		}
	}
	return result
}

func outputName(track string, index int) string {
	if index == 0 {
		return track + ".mp4"
	}
	return fmt.Sprintf("%s.part%02d.mp4", track, index+1)
}

func safeMediaURL(indexURL, source string) (string, error) {
	base, err := url.Parse(indexURL)
	if err != nil {
		return "", err
	}
	relative, err := url.Parse(source)
	if err != nil {
		return "", err
	}
	return base.ResolveReference(relative).String(), nil
}

func emptyArtifacts() map[string][]domain.VideoArtifact {
	return map[string][]domain.VideoArtifact{"Video1": {}, "Video2": {}, "VGA": {}}
}

func anyArtifacts(values map[string][]domain.VideoArtifact) bool {
	for _, items := range values {
		if len(items) > 0 {
			return true
		}
	}
	return false
}
