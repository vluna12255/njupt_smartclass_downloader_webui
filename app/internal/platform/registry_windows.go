//go:build windows

package platform

import (
	"fmt"
	"syscall"
	"unsafe"
)

const (
	hkeyLocalMachine = 0x80000002
	keyRead          = 0x20019
	regDword         = 4
)

var (
	procRegOpenKeyEx  = advapi32.NewProc("RegOpenKeyExW")
	procRegQueryValue = advapi32.NewProc("RegQueryValueExW")
	procRegCloseKey   = advapi32.NewProc("RegCloseKey")
)

func IsVCRedistInstalled() (bool, error) {
	keyPath, _ := syscall.UTF16PtrFromString(`SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64`)
	var handle uintptr
	result, _, _ := procRegOpenKeyEx.Call(hkeyLocalMachine, uintptr(unsafe.Pointer(keyPath)), 0, keyRead, uintptr(unsafe.Pointer(&handle)))
	if result != 0 {
		return false, nil
	}
	defer procRegCloseKey.Call(handle)
	valueName, _ := syscall.UTF16PtrFromString("Installed")
	var valueType uint32
	var value uint32
	size := uint32(unsafe.Sizeof(value))
	result, _, _ = procRegQueryValue.Call(
		handle, uintptr(unsafe.Pointer(valueName)), 0, uintptr(unsafe.Pointer(&valueType)),
		uintptr(unsafe.Pointer(&value)), uintptr(unsafe.Pointer(&size)),
	)
	if result != 0 {
		return false, nil
	}
	if valueType != regDword {
		return false, fmt.Errorf("unexpected VC Runtime registry type: %d", valueType)
	}
	return value == 1, nil
}
