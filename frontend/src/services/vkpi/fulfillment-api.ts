import { apiFetch, jsonBody } from "../http";

// 履约后半链(观察窗口 + 已匹配内容帖)只读/复核 API。
// 对应后端 backend/app/api/routers/vkpi_projects_fulfillment.py:
//   GET   /api/admin/vkpi/projects/observation-windows?status=&project_id=
//   GET   /api/admin/vkpi/projects/content-posts?status=&project_id=
//   PATCH /api/admin/vkpi/projects/content-posts/{post_id}        body {action, note?}
//   POST  /api/admin/vkpi/projects/{project_id}/content-posts/advance-retrospective
// 红线:这些端点零 fit 写、零自动裁决;仅置 content_posts.status + 回填窗口 matched_content_post_id。

export interface VkpiObservationWindow {
  id: number;
  project_id: number;
  assignment_id: number | null;
  kol_pool_id: number | null;
  starts_at: string | null;
  ends_at: string | null;
  status: string;
  scan_count: number | null;
  last_scan_at: string | null;
  matched_content_post_id: number | null;
  project_name?: string | null;
  product_name?: string | null;
}

export interface VkpiContentPost {
  id: number;
  project_id: number;
  assignment_id: number | null;
  kol_pool_id: number | null;
  evidence_id: number | null;
  platform: string | null;
  content_url: string | null;
  title: string | null;
  published_at: string | null;
  view_count: number | null;
  like_count: number | null;
  comment_count: number | null;
  match_confidence: number | null;
  match_reason: string | null;
  status: string;
  project_name?: string | null;
  product_name?: string | null;
}

export interface VkpiObservationWindowList {
  status: string;
  count: number;
  items: VkpiObservationWindow[];
  filter_status?: string;
  note?: string;
}

export interface VkpiContentPostList {
  status: string;
  count: number;
  items: VkpiContentPost[];
  filter_status?: string;
  note?: string;
}

export async function listObservationWindows(
  token: string,
  projectId: string | number,
  status = "all",
): Promise<VkpiObservationWindowList> {
  const params = new URLSearchParams({ status, project_id: String(projectId) });
  return apiFetch<VkpiObservationWindowList>(
    `/api/admin/vkpi/projects/observation-windows?${params.toString()}`,
    {},
    token,
  );
}

export async function listContentPosts(
  token: string,
  projectId: string | number,
  status = "all",
): Promise<VkpiContentPostList> {
  const params = new URLSearchParams({ status, project_id: String(projectId) });
  return apiFetch<VkpiContentPostList>(
    `/api/admin/vkpi/projects/content-posts?${params.toString()}`,
    {},
    token,
  );
}

export async function reviewContentPost(
  token: string,
  postId: number,
  action: "matched" | "rejected" | "needs_review",
  note = "",
): Promise<{ status: string; action?: string; observation_window_id?: number | null }> {
  return apiFetch(
    `/api/admin/vkpi/projects/content-posts/${encodeURIComponent(String(postId))}`,
    { method: "PATCH", body: jsonBody({ action, note }) },
    token,
  );
}

export async function advanceContentPostsToRetrospective(
  token: string,
  projectId: string | number,
): Promise<Record<string, unknown>> {
  return apiFetch(
    `/api/admin/vkpi/projects/${encodeURIComponent(String(projectId))}/content-posts/advance-retrospective`,
    { method: "POST", timeoutMs: 60000 },
    token,
  );
}
