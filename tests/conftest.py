"""测试隔离：运行记录写到临时目录，避免污染项目 data/。"""
import os
import tempfile

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="schemaevo-test-")
