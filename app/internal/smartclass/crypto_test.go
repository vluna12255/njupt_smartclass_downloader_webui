package smartclass

import "testing"

func TestEncryptSSOField(t *testing.T) {
	got, err := EncryptSSOField("B23060714", "1760000000000")
	if err != nil {
		t.Fatal(err)
	}
	const want = "3eaad1d9adf02341cbd58932c648b0e2"
	if got != want {
		t.Fatalf("EncryptSSOField() = %s, want %s", got, want)
	}
}

func TestPKCS7RejectsInvalidPadding(t *testing.T) {
	if _, err := pkcs7Unpad([]byte("1234567890123450"), 16); err == nil {
		t.Fatal("expected invalid padding error")
	}
}
