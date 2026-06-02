//go:build windows

package platform

import (
	"context"
	"fmt"
	"syscall"
	"unsafe"
)

const (
	credTypeGeneric       = 1
	credPersistEnterprise = 3
	errorNotFound         = syscall.Errno(1168)
)

var (
	advapi32       = syscall.NewLazyDLL("advapi32.dll")
	procCredWrite  = advapi32.NewProc("CredWriteW")
	procCredRead   = advapi32.NewProc("CredReadW")
	procCredDelete = advapi32.NewProc("CredDeleteW")
	procCredFree   = advapi32.NewProc("CredFree")
)

type windowsCredentialStore struct{}

type windowsCredential struct {
	Flags              uint32
	Type               uint32
	TargetName         *uint16
	Comment            *uint16
	LastWritten        syscall.Filetime
	CredentialBlobSize uint32
	CredentialBlob     *byte
	Persist            uint32
	AttributeCount     uint32
	Attributes         uintptr
	TargetAlias        *uint16
	UserName           *uint16
}

func NewCredentialStore() CredentialStore {
	return windowsCredentialStore{}
}

func (store windowsCredentialStore) Get(_ context.Context, service, username string) (string, bool, error) {
	password, storedUsername, ok, err := store.read(service)
	if err != nil {
		return "", false, err
	}
	if ok && (username == "" || username == storedUsername) {
		return password, true, nil
	}
	password, storedUsername, ok, err = store.read(compoundCredentialTarget(service, username))
	if err != nil || !ok || username != storedUsername {
		return "", false, err
	}
	return password, true, nil
}

func (store windowsCredentialStore) read(targetName string) (string, string, bool, error) {
	target, err := syscall.UTF16PtrFromString(targetName)
	if err != nil {
		return "", "", false, err
	}
	var value *windowsCredential
	result, _, callErr := procCredRead.Call(uintptr(unsafe.Pointer(target)), credTypeGeneric, 0, uintptr(unsafe.Pointer(&value)))
	if result == 0 {
		if callErr == errorNotFound {
			return "", "", false, nil
		}
		return "", "", false, fmt.Errorf("CredReadW: %w", callErr)
	}
	defer procCredFree.Call(uintptr(unsafe.Pointer(value)))
	username := utf16PointerString(value.UserName)
	if value.CredentialBlob == nil || value.CredentialBlobSize == 0 {
		return "", username, true, nil
	}
	raw := unsafe.Slice(value.CredentialBlob, int(value.CredentialBlobSize))
	words := make([]uint16, len(raw)/2)
	for index := range words {
		words[index] = uint16(raw[index*2]) | uint16(raw[index*2+1])<<8
	}
	return syscall.UTF16ToString(words), username, true, nil
}

func (store windowsCredentialStore) Set(_ context.Context, service, username, password string) error {
	existingPassword, existingUsername, ok, err := store.read(service)
	if err != nil {
		return err
	}
	if ok && existingUsername != username {
		if err := store.write(compoundCredentialTarget(service, existingUsername), existingUsername, existingPassword); err != nil {
			return err
		}
	}
	return store.write(service, username, password)
}

func (windowsCredentialStore) write(targetName, username, password string) error {
	target, err := syscall.UTF16PtrFromString(targetName)
	if err != nil {
		return err
	}
	user, err := syscall.UTF16PtrFromString(username)
	if err != nil {
		return err
	}
	words, err := syscall.UTF16FromString(password)
	if err != nil {
		return err
	}
	words = words[:len(words)-1]
	raw := make([]byte, len(words)*2)
	for index, word := range words {
		raw[index*2] = byte(word)
		raw[index*2+1] = byte(word >> 8)
	}
	credential := windowsCredential{
		Type:               credTypeGeneric,
		TargetName:         target,
		Persist:            credPersistEnterprise,
		UserName:           user,
		CredentialBlobSize: uint32(len(raw)),
	}
	if len(raw) > 0 {
		credential.CredentialBlob = &raw[0]
	}
	result, _, callErr := procCredWrite.Call(uintptr(unsafe.Pointer(&credential)), 0)
	if result == 0 {
		return fmt.Errorf("CredWriteW: %w", callErr)
	}
	return nil
}

func (store windowsCredentialStore) Delete(_ context.Context, service, username string) error {
	for _, target := range []string{service, compoundCredentialTarget(service, username)} {
		_, storedUsername, ok, err := store.read(target)
		if err != nil {
			return err
		}
		if ok && storedUsername == username {
			if err := store.deleteTarget(target); err != nil {
				return err
			}
		}
	}
	return nil
}

func (windowsCredentialStore) deleteTarget(targetName string) error {
	target, err := syscall.UTF16PtrFromString(targetName)
	if err != nil {
		return err
	}
	result, _, callErr := procCredDelete.Call(uintptr(unsafe.Pointer(target)), credTypeGeneric, 0)
	if result == 0 && callErr != errorNotFound {
		return fmt.Errorf("CredDeleteW: %w", callErr)
	}
	return nil
}

func compoundCredentialTarget(service, username string) string {
	return username + "@" + service
}

func utf16PointerString(value *uint16) string {
	if value == nil {
		return ""
	}
	words := (*[1 << 15]uint16)(unsafe.Pointer(value))
	length := 0
	for length < len(words) && words[length] != 0 {
		length++
	}
	return syscall.UTF16ToString(words[:length])
}
