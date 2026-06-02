import { useState } from 'react';
import { scanFolder, importFolder, listFlights, deleteFlight, type Flight } from '../api';

interface Props {
  onImported: () => void;
}

export default function ImportPage({ onImported }: Props) {
  const [path, setPath] = useState('');
  const [scanning, setScanning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [preview, setPreview] = useState<{ drone_id: string; file_count: number; data_types: Record<string, number> }[]>([]);
  const [result, setResult] = useState<string>('');
  const [flights, setFlights] = useState<Flight[]>([]);

  const handleScan = async () => {
    if (!path.trim()) return;
    setScanning(true);
    setPreview([]);
    setResult('');
    try {
      const data = await scanFolder(path.trim());
      if (data.error) {
        setResult('错误: ' + data.error);
      } else if (data.files) {
        setPreview(data.files);
      }
    } catch (e: any) {
      setResult('扫描失败: ' + e.message);
    } finally {
      setScanning(false);
    }
  };

  const handleImport = async () => {
    if (!path.trim()) return;
    setImporting(true);
    setResult('');
    try {
      const data = await importFolder(path.trim());
      if (data.error) {
        setResult('导入失败: ' + data.error);
      } else if (data.imported) {
        const lines = data.imported.map(
          (f) => `✓ UAV${f.drone_id}: ${f.rows} 行数据 (${Object.keys(f.details).join(', ')})`
        );
        setResult(lines.join('\n'));
        onImported();
        loadFlights();
      }
    } catch (e: any) {
      setResult('导入失败: ' + e.message);
    } finally {
      setImporting(false);
    }
  };

  const loadFlights = async () => {
    try {
      const data = await listFlights();
      setFlights(data.flights);
    } catch { /* ignore */ }
  };

  const handleDelete = async (id: number) => {
    await deleteFlight(id);
    loadFlights();
    onImported();
  };

  return (
    <div className="h-full overflow-auto p-8 max-w-4xl mx-auto space-y-8">
      {/* Import Section */}
      <section>
        <h2 className="text-xl font-semibold text-gray-900 mb-4">导入飞行数据</h2>
        <div className="flex gap-3">
          <input
            type="text"
            value={path}
            onChange={(e) => setPath(e.target.value)}
            placeholder="输入飞行数据文件夹路径，如 D:\data\20250323153351_535"
            className="flex-1 bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500"
          />
          <button
            onClick={handleScan}
            disabled={scanning || !path.trim()}
            className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700 transition-colors"
          >
            {scanning ? '扫描中...' : '扫描'}
          </button>
          <button
            onClick={handleImport}
            disabled={importing || !path.trim()}
            className="px-4 py-2 bg-blue-600 hover:bg-blue-500 disabled:opacity-40 rounded-lg text-sm font-medium text-white transition-colors"
          >
            {importing ? '导入中...' : '导入'}
          </button>
        </div>
      </section>

      {/* Preview */}
      {preview.length > 0 && (
        <section>
          <h3 className="text-sm font-medium text-gray-500 mb-3">扫描结果</h3>
          <div className="space-y-2">
            {preview.map((p) => (
              <div key={p.drone_id} className="bg-gray-50 rounded-lg p-4 border border-gray-200">
                <div className="flex items-center gap-2 mb-2">
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                    UAV{p.drone_id}
                  </span>
                  <span className="text-sm text-gray-500">{p.file_count} 个数据文件</span>
                </div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(p.data_types).map(([type, count]) => (
                    <span key={type} className="px-2 py-0.5 bg-gray-100 rounded text-xs text-gray-600">
                      {type} ×{count}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Result */}
      {result && (
        <section>
          <div className="bg-gray-50 rounded-lg p-4 border border-gray-200">
            <pre className="text-sm text-green-600 whitespace-pre-wrap font-mono">{result}</pre>
          </div>
        </section>
      )}

      {/* Imported Flights */}
      <section>
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-xl font-semibold text-gray-900">已导入飞行</h2>
          <button onClick={loadFlights} className="text-xs text-blue-600 hover:text-blue-500">
            刷新
          </button>
        </div>
        {flights.length === 0 ? (
          <p className="text-sm text-gray-400">暂无已导入的飞行数据</p>
        ) : (
          <div className="space-y-2">
            {flights.map((f) => (
              <div key={f.id} className="flex items-center justify-between bg-white rounded-lg px-4 py-3 border border-gray-200">
                <div className="flex items-center gap-4">
                  <span className="px-2 py-0.5 bg-blue-100 text-blue-700 rounded text-xs font-medium">
                    UAV{f.drone_id}
                  </span>
                  <span className="text-sm font-medium text-gray-800">{f.name}</span>
                  <span className="text-xs text-gray-400">{f.flight_date}</span>
                  {f.duration_sec && (
                    <span className="text-xs text-gray-400">
                      {Math.round(f.duration_sec / 60)}分钟
                    </span>
                  )}
                </div>
                <button
                  onClick={() => handleDelete(f.id)}
                  className="text-xs text-red-500 hover:text-red-600 px-2 py-1 rounded hover:bg-red-50"
                >
                  删除
                </button>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
