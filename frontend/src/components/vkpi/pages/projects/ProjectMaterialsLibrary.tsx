// P1 物料库最小可用(2026-07-03):上传 + 列表 + 下载。
// 完全复用既有 evidence uploads 落盘(onUploadEvidenceFile → POST /evidence/uploads),
// 归档记录走 /api/marketing/projects/{id}/materials(后端复用 vkpi_content_assets,
// asset_type='material' 区分,零新表)。下载即静态 /uploads 直链。
import { useCallback, useEffect, useRef, useState } from 'react';
import { Download, FileText, Loader2, RefreshCw, Upload } from 'lucide-react';
import { apiFetch, buildApiUrl } from '../../../../services/http';

interface MaterialMetadata {
  file_name?: string;
  file_type?: string;
  size?: number;
  note?: string;
}

interface MaterialItem {
  id: number;
  project_id: number;
  asset_url: string;
  created_at?: string;
  metadata?: MaterialMetadata;
}

interface MaterialsListResponse {
  items?: MaterialItem[];
  count?: number;
}

// 与后端 SAFE_EXTENSIONS 对齐:超类型直接在选择器挡掉,少一次 400 往返。
const ACCEPT_EXTENSIONS = '.pdf,.png,.jpg,.jpeg,.webp,.gif,.csv,.xlsx,.xls,.txt,.doc,.docx';

function formatBytes(size?: number) {
  const value = Number(size || 0);
  if (!value) return '';
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MB`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${value} B`;
}

function shortDate(value?: string) {
  const raw = String(value || '').trim();
  if (!raw) return '—';
  const parsed = new Date(raw);
  if (Number.isNaN(parsed.getTime())) return raw.slice(0, 16);
  return parsed.toLocaleString('zh-CN', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

function materialName(item: MaterialItem) {
  const metaName = String(item.metadata?.file_name || '').trim();
  if (metaName) return metaName;
  const tail = String(item.asset_url || '').split('/').pop() || '';
  return tail || `物料 #${item.id}`;
}

export function ProjectMaterialsLibrary({
  apiToken,
  projectId,
  onUploadEvidenceFile,
}: {
  apiToken?: string;
  projectId: string;
  onUploadEvidenceFile?: (file: File, payload?: { entityType?: string; entityId?: string; purpose?: string }) => Promise<Record<string, unknown>>;
}) {
  const [items, setItems] = useState<MaterialItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState('');
  const fileInputRef = useRef<HTMLInputElement>(null);

  const loadMaterials = useCallback(async () => {
    if (!apiToken || !projectId) return;
    setLoading(true);
    setError('');
    try {
      const resp = await apiFetch<MaterialsListResponse>(`/api/marketing/projects/${projectId}/materials`, {}, apiToken);
      setItems(Array.isArray(resp.items) ? resp.items : []);
    } catch (err) {
      // 错误必须显在区块里(静默 catch 禁令)。
      setError(err instanceof Error ? err.message : '物料列表加载失败。');
    } finally {
      setLoading(false);
    }
  }, [apiToken, projectId]);

  useEffect(() => {
    void loadMaterials();
  }, [loadMaterials]);

  const handlePickedFile = async (file: File | null) => {
    if (!file) return;
    if (!apiToken || !onUploadEvidenceFile) {
      setError(!apiToken ? '缺少 API token,无法上传物料。' : '当前环境缺少文件上传接口。');
      return;
    }
    setUploading(true);
    setError('');
    try {
      // 第一步:复用既有 evidence uploads 落盘(25MB 上限、扩展名白名单由后端统一把关)。
      const uploaded = await onUploadEvidenceFile(file, {
        entityType: 'project_material',
        entityId: String(projectId),
        purpose: 'material',
      });
      const fileUrl = String(uploaded.file_url || uploaded.fileUrl || '');
      if (!fileUrl) throw new Error('文件上传未返回地址。');
      // 第二步:登记进项目物料库(按 project_id 归档,列表由此读回)。
      await apiFetch(
        `/api/marketing/projects/${projectId}/materials`,
        {
          method: 'POST',
          body: JSON.stringify({
            asset_url: fileUrl,
            file_name: String(uploaded.file_name || file.name || ''),
            file_type: String(uploaded.file_type || file.type || ''),
            size: Number(uploaded.size || file.size || 0),
          }),
        },
        apiToken,
      );
      await loadMaterials();
    } catch (err) {
      setError(err instanceof Error ? err.message : '物料上传失败。');
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  return (
    <div className="rounded-lg border border-white/[0.06] bg-white/[0.015] overflow-hidden" aria-label="项目物料库">
      <div className="px-4 py-2.5 border-b border-white/[0.05] flex items-center justify-between gap-3">
        <div>
          <h4 className="text-[12px] font-semibold text-white">物料库</h4>
          <div className="text-[9.5px] text-slate-500 mt-0.5">产品图 / 参数手册 / 脚本等项目物料,按项目归档,点击文件名或下载图标获取。</div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            type="button"
            className="rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-white flex items-center gap-1 disabled:opacity-50"
            disabled={loading}
            onClick={() => void loadMaterials()}
          >
            <RefreshCw size={10} className={loading ? 'animate-spin' : ''} />刷新
          </button>
          <button
            type="button"
            className="rounded-md border border-purple-400/30 bg-purple-500/10 px-2.5 py-1 text-[10.5px] font-semibold text-purple-200 hover:bg-purple-500/20 flex items-center gap-1 disabled:opacity-50"
            disabled={uploading || !apiToken || !onUploadEvidenceFile}
            onClick={() => fileInputRef.current?.click()}
            title="上传物料文件(25MB 内;pdf / 图片 / 表格 / 文档)"
          >
            {uploading ? <Loader2 size={10} className="animate-spin" /> : <Upload size={10} />}
            {uploading ? '上传中…' : '上传物料'}
          </button>
          <input
            ref={fileInputRef}
            type="file"
            accept={ACCEPT_EXTENSIONS}
            className="hidden"
            onChange={(event) => void handlePickedFile(event.target.files?.[0] || null)}
          />
        </div>
      </div>

      {error ? (
        <div className="px-4 py-2 text-[10.5px] text-red-300 bg-red-500/[0.06] border-b border-red-500/15">{error}</div>
      ) : null}

      {loading && !items.length ? (
        <div className="px-4 py-6 text-center text-[10.5px] text-slate-500 flex items-center justify-center gap-1.5">
          <Loader2 size={11} className="animate-spin" />物料列表加载中…
        </div>
      ) : items.length ? (
        <div className="divide-y divide-white/[0.04]">
          {items.map((item) => {
            const name = materialName(item);
            const href = buildApiUrl(item.asset_url);
            const sizeLabel = formatBytes(item.metadata?.size);
            return (
              <div key={item.id} className="px-4 py-2.5 flex items-center gap-3">
                <FileText size={13} className="text-slate-400 shrink-0" />
                <div className="flex-1 min-w-0">
                  <a
                    className="text-[11.5px] text-slate-200 hover:text-purple-200 font-medium truncate block"
                    href={href}
                    target="_blank"
                    rel="noreferrer"
                    title={`打开 ${name}`}
                  >
                    {name}
                  </a>
                  <div className="text-[9.5px] text-slate-500">
                    {shortDate(item.created_at)}
                    {sizeLabel ? ` · ${sizeLabel}` : ''}
                    {item.metadata?.file_type ? ` · ${item.metadata.file_type}` : ''}
                  </div>
                </div>
                <a
                  className="shrink-0 rounded-md border border-white/[0.08] bg-white/[0.03] px-2 py-1 text-[10px] text-slate-300 hover:bg-white/[0.06] hover:text-white flex items-center gap-1"
                  href={href}
                  download={name}
                  target="_blank"
                  rel="noreferrer"
                >
                  <Download size={10} />下载
                </a>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="px-4 py-6 text-center">
          <div className="text-[11px] text-slate-400 mb-1">暂无物料</div>
          <div className="text-[10px] text-slate-500">点右上「上传物料」把产品图 / 参数手册 / 脚本归档到当前项目。</div>
        </div>
      )}
    </div>
  );
}
