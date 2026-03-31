#!/usr/bin/env python
# -*- coding: utf-8 -*-
path = r"d:\1111111\app\src\services\task_manager.py"

with open(path, 'rb') as f:
    content = f.read()

marker = b"# \xe9\x80\x9a\xe7\x9f\xa5\xe6\x89\x80\xe6\x9c\x89\xe7\xad\x89\xe5\xbe\x85\xe7\x9a\x84\xe4\xbb\xbb\xe5\x8a\xa1"
idx = content.find(marker)
print(f"Found marker at: {idx}")

if idx < 0:
    print("ERROR: marker not found")
else:
    line_start = content.rfind(b'\n', 0, idx) + 1
    search_from = idx
    else_marker = b"                        else:"
    else_idx = content.find(else_marker, search_from)
    print(f"Found else at: {else_idx}")
    
    logger_end = content.rfind(b'\n', search_from, else_idx)
    section_end = logger_end + 1
    
    old_section = content[line_start:section_end]
    print(f"Old section length: {len(old_section)}")
    
    new_section = (
        b"                            # \xe6\xa3\x80\xe6\x9f\xa5\xe5\x90\xaf\xe5\x8a\xa8\xe4\xbb\xbb\xe5\x8a\xa1\xe5\x8d\xa1\xe7\x89\x87\xef\xbc\x9a\xe8\xa5\xbf\xe8\xa3\x85\xe8\x81\x9a\xe9\x85\xae\xe7\xad\x89\xe7\xad\x89 global_model \xe4\xb8\xba None\r\n"
        b"                            # \xe4\xbd\x86\xe6\x9c\x8d\xe5\x8a\xa1\xe8\x99\xbd\xe7\x84\xb6\xe5\x93\x8d\xe5\xba\xa4 /docs\xef\xbc\x8c\xe9\x9c\x80\xe5\x9c\xa8\xe6\xad\xa4\xe6\x8b\xa6\xe6\x88\xaa\r\n"
        b"                            _startup_tid = f\"startup_{target_plugin_name}\"\r\n"
        b"                            with self.lock:\r\n"
        b"                                _st = self.tasks.get(_startup_tid)\r\n"
        b"                                _startup_failed = _st is not None and _st.status == TaskStatus.FAILED\r\n"
        b"                                _startup_error = _st.error if _st else \"\"\r\n"
        b"                            if _startup_failed:\r\n"
        b"                                # \xe5\x94\xa4\xe9\x86\x92\xe6\x89\x80\xe6\x9c\x89\xe6\xad\xa3\xe5\x9c\xa8 wait \xe7\x9a\x84\xe9\x9d\x9e starter \xe4\xbb\xbb\xe5\x8a\xa1\r\n"
        b"                                event.set()\r\n"
        b"                                with self._plugin_ready_lock:\r\n"
        b"                                    self._plugin_ready_events.pop(target_plugin_name, None)\r\n"
        b"                                raise Exception(f\"{target_plugin_name} \xe6\xa8\xa1\xe5\x9e\x8b\xe5\x8a\xa0\xe8\xbd\xbd\xe5\xa4\xb1\xe8\xb4\xa5: {_startup_error}\")\r\n"
        b"\r\n"
        b"                            # \xe9\x80\x9a\xe7\x9f\xa5\xe6\x89\x80\xe6\x9c\x89\xe7\xad\x89\xe5\xbe\x85\xe7\x9a\x84\xe4\xbb\xbb\xe5\x8a\xa1\xef\xbc\x9a\xe6\x9c\x8d\xe5\x8a\xa1\xe5\xb7\xb2\xe5\xb0\xb1\xe7\xbb\xaa\r\n"
        b"                            event.set()\r\n"
        b"                            logger.info(f\"Task {task_id}: {target_plugin_name} \xe6\x9c\x8d\xe5\x8a\xa1\xe5\xb0\xb1\xe7\xbb\xaa\xef\xbc\x8c\xe9\x80\x9a\xe7\x9f\xa5\xe5\x85\xb6\xe4\xbb\x96\xe7\xad\x89\xe5\xbe\x85\xe4\xbb\xbb\xe5\x8a\xa1\")\r\n"
    )
    
    result = content[:line_start] + new_section + content[section_end:]
    
    with open(path, 'wb') as f:
        f.write(result)
    
    print("OK: patch applied")
