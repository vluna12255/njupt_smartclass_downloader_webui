package smartclass

import (
	"bytes"
	"crypto/aes"
	"crypto/cipher"
	"encoding/hex"
	"fmt"
)

func EncryptSSOField(value, checkKey string) (string, error) {
	key := []byte("iam" + checkKey)
	if len(key) != aes.BlockSize {
		return "", fmt.Errorf("invalid SSO key length: %d", len(key))
	}
	block, err := aes.NewCipher(key)
	if err != nil {
		return "", err
	}
	body := pkcs7Pad([]byte(value), aes.BlockSize)
	cipher.NewCBCEncrypter(block, key).CryptBlocks(body, body)
	return hex.EncodeToString(body), nil
}

func DecryptDomainConfig(ciphertext string) ([]byte, error) {
	body, err := hex.DecodeString(ciphertext)
	if err != nil {
		return nil, err
	}
	key := []byte("80bdbdbaf7494add99198960d715d41b")
	iv := []byte("bdbaf7494add9919")
	block, err := aes.NewCipher(key)
	if err != nil {
		return nil, err
	}
	if len(body)%aes.BlockSize != 0 {
		return nil, fmt.Errorf("domain config ciphertext is not block aligned")
	}
	cipher.NewCBCDecrypter(block, iv).CryptBlocks(body, body)
	return pkcs7Unpad(body, aes.BlockSize)
}

func pkcs7Pad(body []byte, blockSize int) []byte {
	padding := blockSize - len(body)%blockSize
	return append(body, bytes.Repeat([]byte{byte(padding)}, padding)...)
}

func pkcs7Unpad(body []byte, blockSize int) ([]byte, error) {
	if len(body) == 0 || len(body)%blockSize != 0 {
		return nil, fmt.Errorf("invalid PKCS#7 body length")
	}
	padding := int(body[len(body)-1])
	if padding < 1 || padding > blockSize || padding > len(body) {
		return nil, fmt.Errorf("invalid PKCS#7 padding")
	}
	for _, value := range body[len(body)-padding:] {
		if int(value) != padding {
			return nil, fmt.Errorf("invalid PKCS#7 padding")
		}
	}
	return body[:len(body)-padding], nil
}
