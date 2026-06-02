function validateForm() {
            var username = document.getElementById('username').value.trim();
            var password = document.getElementById('password').value.trim();
            var errorDiv = document.getElementById('form-error');
            if (!username || !password) {
                errorDiv.textContent = '请输入用户名及密码';
                errorDiv.classList.remove('hidden');
                return false;
            }
            errorDiv.classList.add('hidden');
            return true;
        }
