// Match SmartClass's cover display: direct CDN images, otherwise a 1s HEAD probe.
(() => {
    const defaultCovers = ['300168686', '302961851', '60921691', '794015686', '730882288', '744441511'];
    const pending = new Set();

    window.cancelVideoCoverLoads = () => {
        pending.forEach(controller => controller.abort());
        pending.clear();
    };

    window.initializeVideoCovers = container => {
        container.querySelectorAll('img[data-cover-url]').forEach(async img => {
            const fallback = `/static/images/covers/${defaultCovers[Math.floor(Math.random() * defaultCovers.length)]}.jpg`;
            const showDefault = () => {
                img.onerror = null;
                img.src = fallback;
                img.alt = '课程默认封面';
            };
            img.onerror = showDefault;
            const raw = img.dataset.coverUrl.trim().replace(/\\/g, '/');
            if (!raw) {
                showDefault();
                return;
            }
            let url;
            try {
                url = new URL(raw, 'https://njupt.smartclass.cn/');
                if (!['https:', 'http:'].includes(url.protocol)) throw new Error('Invalid cover URL');
            } catch {
                showDefault();
                return;
            }
            if (url.hostname === 'staticfilesnew.smartclass.cn') {
                img.src = url.href;
                return;
            }
            const controller = new AbortController();
            pending.add(controller);
            const timeout = setTimeout(() => controller.abort(), 1000);
            try {
                const response = await fetch(url.href, { method: 'HEAD', signal: controller.signal });
                if (!response.ok && response.status !== 304) throw new Error('Cover unavailable');
                if (img.isConnected) img.src = url.href;
            } catch {
                if (img.isConnected) showDefault();
            } finally {
                clearTimeout(timeout);
                pending.delete(controller);
            }
        });
    };
})();
