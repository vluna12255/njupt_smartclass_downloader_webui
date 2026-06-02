package aria2

import "path/filepath"

func filepathDir(path string) string  { return filepath.Dir(path) }
func filepathBase(path string) string { return filepath.Base(path) }
