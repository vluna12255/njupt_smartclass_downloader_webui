package smartclass

import (
	"encoding/json"
	"fmt"
	"time"

	"smartclassdownloader/internal/domain"
)

func parseSearchResponse(body []byte) (domain.VideoSearchResult, error) {
	var root map[string]any
	if err := json.Unmarshal(body, &root); err != nil {
		return domain.VideoSearchResult{}, err
	}
	if success, ok := boolField(root, "Success", "success"); ok && !success {
		return domain.VideoSearchResult{}, fmt.Errorf("search failed: %s", stringField(root, "Message", "message"))
	}
	value := first(root, "Value", "value")
	if value == nil || value == "" {
		return domain.VideoSearchResult{}, nil
	}
	container, ok := value.(map[string]any)
	if !ok {
		container = root
	}
	rawRows := first(container, "Data", "data", "rows")
	rows, _ := rawRows.([]any)
	result := domain.VideoSearchResult{TotalCount: len(rows)}
	if total := numberField(root, "TotalCount", "totalCount"); total > 0 {
		result.TotalCount = int(total)
	}
	for _, item := range rows {
		row, ok := item.(map[string]any)
		if !ok {
			continue
		}
		start, err := parseShanghaiTime(stringField(row, "StartTime"))
		if err != nil {
			return domain.VideoSearchResult{}, err
		}
		stop, err := parseShanghaiTime(stringField(row, "StopTime"))
		if err != nil {
			return domain.VideoSearchResult{}, err
		}
		result.Videos = append(result.Videos, domain.VideoSummary{
			ID: stringField(row, "NewID"), Title: stringField(row, "Title"),
			StartTime: start, StopTime: stop, CourseName: stringField(row, "CourseName"),
			Teachers: stringField(row, "Teachers"), ClassroomName: stringField(row, "ClassRoomName"),
			CoverURL: stringField(row, "Cover"),
		})
	}
	return result, nil
}

func parseVideoInfoResponse(body []byte) (domain.VideoInfo, error) {
	var root map[string]any
	if err := json.Unmarshal(body, &root); err != nil {
		return domain.VideoInfo{}, err
	}
	if success, ok := boolField(root, "Success", "success"); ok && !success {
		return domain.VideoInfo{}, fmt.Errorf("get video info failed: %s", stringField(root, "Message", "message"))
	}
	value, ok := first(root, "Value", "value").(map[string]any)
	if !ok {
		return domain.VideoInfo{}, fmt.Errorf("unexpected video info response")
	}
	start, err := parseShanghaiTime(stringField(value, "StartTime"))
	if err != nil {
		return domain.VideoInfo{}, err
	}
	stop, err := parseShanghaiTime(stringField(value, "StopTime"))
	if err != nil {
		return domain.VideoInfo{}, err
	}
	result := domain.VideoInfo{
		ID: stringField(value, "NewID"), Title: stringField(value, "Title"),
		StartTime: start, StopTime: stop, CourseName: stringField(value, "CourseName"),
	}
	rawSegments, _ := first(value, "VideoSegmentInfo", "videoSegmentInfo").([]any)
	for _, item := range rawSegments {
		segment, ok := item.(map[string]any)
		if ok {
			result.Segments = append(result.Segments, domain.VideoSegment{IndexFileURI: stringField(segment, "IndexFileUri")})
		}
	}
	return result, nil
}

func parseShanghaiTime(value string) (time.Time, error) {
	location, err := time.LoadLocation("Asia/Shanghai")
	if err != nil {
		location = time.FixedZone("CST", 8*60*60)
	}
	return time.ParseInLocation("2006-01-02 15:04:05", value, location)
}

func first(values map[string]any, keys ...string) any {
	for _, key := range keys {
		if value, ok := values[key]; ok {
			return value
		}
	}
	return nil
}

func stringField(values map[string]any, keys ...string) string {
	value, _ := first(values, keys...).(string)
	return value
}

func boolField(values map[string]any, keys ...string) (bool, bool) {
	value, ok := first(values, keys...).(bool)
	return value, ok
}

func numberField(values map[string]any, keys ...string) float64 {
	value, _ := first(values, keys...).(float64)
	return value
}
