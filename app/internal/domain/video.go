package domain

import "time"

type VideoSearchCondition struct {
	TitleKey   string
	PageSize   int
	PageNumber int
	Sort       string
	Order      int
	StartDate  string
	EndDate    string
}

type VideoSummary struct {
	ID            string
	Title         string
	StartTime     time.Time
	StopTime      time.Time
	CourseName    string
	Teachers      string
	ClassroomName string
	CoverURL      string
}

type VideoSegment struct {
	IndexFileURI string
}

type VideoInfo struct {
	ID         string
	Title      string
	StartTime  time.Time
	StopTime   time.Time
	CourseName string
	Segments   []VideoSegment
}

type VideoSearchResult struct {
	TotalCount int
	Videos     []VideoSummary
}

type VideoArtifact struct {
	TrackType    string
	Path         string
	URL          string
	SegmentIndex int
}

type CourseRequest struct {
	TaskID             string
	VideoID            string
	Title              string
	TargetTypes        []string
	TranscribeTargets  []string
	ASRServiceURL      string
	PluginDependencies []string
}
