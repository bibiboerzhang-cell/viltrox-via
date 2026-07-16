import { useCallback, useEffect, useState } from 'react';
import { getKolProfile } from '../../../domains/kol';
import { getProjectDetail } from '../../../domains/projects';
import type { VkpiKolProfile, VkpiProjectDetail, VkpiProjectRow } from '../vkpiTypes';
import { textValue } from '../shared/vkpiDataUtils';

interface UseProjectDetailDrawerArgs {
  apiToken?: string;
  selectedProject?: VkpiProjectRow;
  onOpenProject?: (projectId: string) => void;
  onAddProjectMessage?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectContent?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onUpsertProjectTerms?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
  onAddProjectShipment?: (projectId: string, payload: Record<string, unknown>) => Promise<void>;
}

export function useProjectDetailDrawer({
  apiToken,
  selectedProject,
  onOpenProject,
  onAddProjectMessage,
  onAddProjectContent,
  onUpsertProjectTerms,
  onAddProjectShipment,
}: UseProjectDetailDrawerArgs) {
  const [projectDetailId, setProjectDetailId] = useState<string | null>(null);
  const [projectDetail, setProjectDetail] = useState<VkpiProjectDetail | null>(null);
  const [projectKolProfile, setProjectKolProfile] = useState<VkpiKolProfile | null>(null);
  const [projectDetailLoading, setProjectDetailLoading] = useState(false);
  const [projectDetailError, setProjectDetailError] = useState('');

  useEffect(() => {
    if (!apiToken || !selectedProject?.kolId) return;
    let cancelled = false;
    getKolProfile(apiToken, selectedProject.kolId)
      .then((profile) => {
        if (!cancelled) setProjectKolProfile(profile);
      })
      .catch(() => {
        if (!cancelled && !projectDetailId) setProjectKolProfile(null);
      });
    return () => {
      cancelled = true;
    };
  }, [apiToken, selectedProject?.kolId, projectDetailId]);

  const refreshProjectDetail = useCallback(async (projectId: string, fallback?: VkpiProjectRow) => {
    if (!projectId) return;
    if (fallback) {
      setProjectDetail({ project: fallback as unknown as Record<string, unknown>, events: [], links: [], sales_attributions: [], costs: [] });
    }
    setProjectKolProfile(null);
    setProjectDetailError('');
    if (!apiToken) return;
    setProjectDetailLoading(true);
    try {
      const detail = await getProjectDetail(apiToken, projectId, { mode: 'summary', assignmentLimit: 50 });
      setProjectDetail(detail);
      const kolId = textValue(detail.project?.kol_id, '');
      if (kolId && kolId !== '-') {
        try {
          setProjectKolProfile(await getKolProfile(apiToken, kolId));
        } catch {
          setProjectKolProfile(null);
        }
      }
    } catch (error) {
      setProjectDetailError(error instanceof Error ? error.message : '项目详情加载失败');
    } finally {
      setProjectDetailLoading(false);
    }
  }, [apiToken]);

  const openProjectDetail = useCallback((project: VkpiProjectRow) => {
    setProjectDetailId(project.id);
    void refreshProjectDetail(project.id, project);
    onOpenProject?.(project.id);
  }, [onOpenProject, refreshProjectDetail]);

  const closeProjectDetail = useCallback(() => {
    setProjectDetailId(null);
    setProjectDetail(null);
    setProjectKolProfile(null);
    setProjectDetailError('');
  }, []);

  const addMessage = useCallback(async (payload: Record<string, unknown>) => {
    if (!projectDetailId || !onAddProjectMessage) return;
    await onAddProjectMessage(projectDetailId, payload);
    await refreshProjectDetail(projectDetailId);
  }, [onAddProjectMessage, projectDetailId, refreshProjectDetail]);

  const addContent = useCallback(async (payload: Record<string, unknown>) => {
    if (!projectDetailId || !onAddProjectContent) return;
    await onAddProjectContent(projectDetailId, payload);
    await refreshProjectDetail(projectDetailId);
  }, [onAddProjectContent, projectDetailId, refreshProjectDetail]);

  const upsertTerms = useCallback(async (payload: Record<string, unknown>) => {
    if (!projectDetailId || !onUpsertProjectTerms) return;
    await onUpsertProjectTerms(projectDetailId, payload);
    await refreshProjectDetail(projectDetailId);
  }, [onUpsertProjectTerms, projectDetailId, refreshProjectDetail]);

  const addShipment = useCallback(async (payload: Record<string, unknown>) => {
    if (!projectDetailId || !onAddProjectShipment) return;
    await onAddProjectShipment(projectDetailId, payload);
    await refreshProjectDetail(projectDetailId);
  }, [onAddProjectShipment, projectDetailId, refreshProjectDetail]);

  return {
    projectDetailId,
    projectDetail,
    projectKolProfile,
    projectDetailLoading,
    projectDetailError,
    openProjectDetail,
    closeProjectDetail,
    addMessage,
    addContent,
    upsertTerms,
    addShipment,
  };
}
