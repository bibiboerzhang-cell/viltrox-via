# Projects Domain

Owns project boards, project details, workflow steps, tasks, and project evidence presentation.

Current migrated slices:
- `projectDetailModel.ts`: project-detail calculations, status helpers, tracking state, analytics summaries, and tab model types.
- `api.ts`: campaign, budget pool, and offboarding API boundary for CampaignsPage.

Page components still render in the legacy shell while business logic moves here.
