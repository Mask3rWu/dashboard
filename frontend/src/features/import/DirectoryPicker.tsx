interface Props {
  path: string;
  browsing: boolean;
  scanning: boolean;
  hasScanResult: boolean;
  onPathChange: (value: string) => void;
  onBrowse: () => void;
  onScan: () => void;
}

export default function DirectoryPicker({ path, browsing, scanning, hasScanResult, onPathChange, onBrowse, onScan }: Props) {
  return (
    <section>
      <h2 className="text-xl font-semibold text-gray-900 mb-4">导入飞行数据</h2>
      <div className="flex gap-3">
        <input type="text" value={path} onChange={(event) => onPathChange(event.target.value)} placeholder="输入飞行数据文件夹路径，或点击浏览选择" className="flex-1 bg-white border border-gray-300 rounded-lg px-4 py-2 text-sm text-gray-800 placeholder-gray-400 focus:outline-none focus:border-blue-500" />
        <button onClick={onBrowse} disabled={browsing} className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700">{browsing ? '...' : '浏览'}</button>
        <button onClick={onScan} disabled={scanning || !path.trim()} className="px-4 py-2 bg-gray-100 hover:bg-gray-200 disabled:opacity-40 rounded-lg text-sm font-medium text-gray-700">{scanning ? '扫描中...' : hasScanResult ? '重新扫描' : '扫描'}</button>
      </div>
    </section>
  );
}
