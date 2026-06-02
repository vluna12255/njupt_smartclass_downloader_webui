package workflow

import "testing"

func TestMultipleSegmentsReceiveStableNames(t *testing.T) {
	if got := outputName("VGA", 0); got != "VGA.mp4" {
		t.Fatalf("outputName first = %s", got)
	}
	if got := outputName("VGA", 1); got != "VGA.part02.mp4" {
		t.Fatalf("outputName second = %s", got)
	}
}
