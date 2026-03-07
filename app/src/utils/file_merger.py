"""
文件合并工具 - 使用内存映射优化大文件合并性能
"""
import os
import mmap
from typing import List

from .logger import get_logger

logger = get_logger('file_merger')


class FileMerger:
    """文件合并器 - 使用内存映射技术"""
    
    @staticmethod
    def merge_chunks_mmap(output_path: str, chunk_files: List[str], 
                         progress_callback=None) -> bool:
        """
        使用内存映射合并分片文件
        
        Args:
            output_path: 输出文件路径
            chunk_files: 分片文件列表(按顺序)
            progress_callback: 进度回调函数 callback(current, total)
            
        Returns:
            是否成功
        """
        try:
            # 验证所有分片文件存在
            for chunk_file in chunk_files:
                if not os.path.exists(chunk_file):
                    raise FileNotFoundError(f"分片文件不存在: {chunk_file}")
            
            # 计算总大小
            total_size = sum(os.path.getsize(f) for f in chunk_files)
            logger.info(f"开始合并 {len(chunk_files)} 个分片, 总大小: {total_size/1024/1024:.2f}MB")
            
            if total_size == 0:
                raise ValueError("分片文件总大小为0")
            
            # 预分配文件空间
            with open(output_path, 'wb') as f:
                f.seek(total_size - 1)
                f.write(b'\0')
            
            logger.debug(f"预分配文件空间: {total_size} bytes")
            
            # 使用内存映射写入
            with open(output_path, 'r+b') as f:
                mm = mmap.mmap(f.fileno(), 0)
                offset = 0
                
                for idx, chunk_file in enumerate(chunk_files):
                    chunk_size = os.path.getsize(chunk_file)
                    
                    # 读取分片数据
                    with open(chunk_file, 'rb') as cf:
                        data = cf.read()
                    
                    # 写入内存映射
                    mm[offset:offset+len(data)] = data
                    offset += len(data)
                    
                    # 进度回调
                    if progress_callback:
                        progress_callback(idx + 1, len(chunk_files))
                    
                    logger.debug(f"合并分片 {idx+1}/{len(chunk_files)}: {chunk_size} bytes")
                
                mm.flush()
                mm.close()
            
            logger.info(f"文件合并完成: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"文件合并失败: {e}", exc_info=True)
            # 清理失败的输出文件
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
            return False
    
    @staticmethod
    def merge_chunks_stream(output_path: str, chunk_files: List[str], 
                           buffer_size: int = 8*1024*1024,
                           progress_callback=None) -> bool:
        """
        使用流式方式合并分片(备用方案,兼容性更好)
        
        Args:
            output_path: 输出文件路径
            chunk_files: 分片文件列表
            buffer_size: 缓冲区大小(默认8MB)
            progress_callback: 进度回调
            
        Returns:
            是否成功
        """
        try:
            logger.info(f"使用流式方式合并 {len(chunk_files)} 个分片")
            
            with open(output_path, 'wb') as outfile:
                for idx, chunk_file in enumerate(chunk_files):
                    if not os.path.exists(chunk_file):
                        raise FileNotFoundError(f"分片文件不存在: {chunk_file}")
                    
                    with open(chunk_file, 'rb') as infile:
                        while True:
                            chunk = infile.read(buffer_size)
                            if not chunk:
                                break
                            outfile.write(chunk)
                    
                    if progress_callback:
                        progress_callback(idx + 1, len(chunk_files))
                    
                    logger.debug(f"合并分片 {idx+1}/{len(chunk_files)}")
            
            logger.info(f"文件合并完成: {output_path}")
            return True
            
        except Exception as e:
            logger.error(f"流式合并失败: {e}", exc_info=True)
            if os.path.exists(output_path):
                try:
                    os.remove(output_path)
                except:
                    pass
            return False
    
    @staticmethod
    def merge_chunks_auto(output_path: str, chunk_files: List[str],
                         progress_callback=None) -> bool:
        """
        自动选择最佳合并方式
        
        Args:
            output_path: 输出文件路径
            chunk_files: 分片文件列表
            progress_callback: 进度回调
            
        Returns:
            是否成功
        """
        total_size = sum(os.path.getsize(f) for f in chunk_files if os.path.exists(f))
        
        # 大于100MB使用内存映射,否则使用流式
        if total_size > 100 * 1024 * 1024:
            logger.info("使用内存映射方式合并(大文件优化)")
            try:
                return FileMerger.merge_chunks_mmap(output_path, chunk_files, progress_callback)
            except Exception as e:
                logger.warning(f"内存映射失败,回退到流式方式: {e}")
                return FileMerger.merge_chunks_stream(output_path, chunk_files, progress_callback)
        else:
            logger.info("使用流式方式合并(小文件)")
            return FileMerger.merge_chunks_stream(output_path, chunk_files, progress_callback)


# 便捷函数
def merge_files(output_path: str, chunk_files: List[str], 
                progress_callback=None) -> bool:
    """
    合并文件的便捷函数
    
    Args:
        output_path: 输出文件路径
        chunk_files: 分片文件列表
        progress_callback: 进度回调
        
    Returns:
        是否成功
    """
    return FileMerger.merge_chunks_auto(output_path, chunk_files, progress_callback)

