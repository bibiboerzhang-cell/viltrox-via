import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  MonoProgressCard,
  MonoUploadComposer,
  type SubmissionProgressSnapshot,
} from "../../components/creator";
import { FloatingViaCat } from "../../components/catographer/FloatingViaCat";
import { BwTopNav } from "../../components/ui";
import { useAuth } from "../../hooks/useAuth";
import { loadCreatorAccountBundle } from "../../services/creator.service";
import type { CreatorProgramResponse } from "../../lib/api";
import type { CreatorSubmission } from "../../types/api";
import { useViaStore } from "../../stores/useViaStore";

function isRecentSubmission(submission: CreatorSubmission) {
  const createdAt = String(submission.created_at || "").trim();
  if (!createdAt) {
    return false;
  }
  const timestamp = new Date(createdAt).getTime();
  if (!Number.isFinite(timestamp)) {
    return false;
  }
  return Date.now() - timestamp <= 1000 * 60 * 60 * 24 * 30;
}

function averageScore(submissions: CreatorSubmission[]) {
  const scores = submissions
    .map((item) => Number(item.overall_score ?? item.final_score ?? 0))
    .filter((value) => Number.isFinite(value) && value > 0);
  if (!scores.length) {
    return 0;
  }
  return Math.round(scores.reduce((sum, value) => sum + value, 0) / scores.length);
}

export default function IndexRoute() {
  const { t } = useTranslation();
  const { status, token, user, refreshUser, openAuthModal } = useAuth();
  const progressSnapshot = useViaStore((state) => state.progressSnapshot);
  const [progress, setProgress] = useState<SubmissionProgressSnapshot>({
    step: 1,
    statusLine: t("home.progressStart"),
  });
  const [submissions, setSubmissions] = useState<CreatorSubmission[]>([]);
  const [program, setProgram] = useState<CreatorProgramResponse | null>(null);

  useEffect(() => {
    if (status !== "authenticated") {
      setProgress({
        step: 1,
        statusLine: t("home.progressStart"),
      });
      return;
    }
    if (progressSnapshot?.surface === "upload") {
      setProgress({
        step: progressSnapshot.step,
        statusLine: progressSnapshot.statusLine,
        jobId: progressSnapshot.jobId,
        sourceLabel: progressSnapshot.sourceLabel,
        sourceKind: progressSnapshot.sourceKind,
      });
      return;
    }
    setProgress((current) =>
      current.jobId
        ? current
        : {
            step: 1,
            statusLine: t("home.progressStart"),
          },
    );
  }, [progressSnapshot, status, t]);

  useEffect(() => {
    let mounted = true;
    if (status !== "authenticated" || !token) {
      setSubmissions([]);
      setProgram(null);
      return () => {
        mounted = false;
      };
    }

    void loadCreatorAccountBundle(token)
      .then((bundle) => {
        if (mounted) {
          setSubmissions(bundle.submissions);
          setProgram(bundle.program);
        }
      })
      .catch(() => {
        if (mounted) {
          setSubmissions([]);
          setProgram(null);
        }
      });

    return () => {
      mounted = false;
    };
  }, [status, token]);

  useEffect(() => {
    if (status !== "authenticated" || !user || !program?.vip) {
      return;
    }
    const nextPoints = Number(program.vip.current_points ?? user.points_total ?? 0);
    const currentPoints = Number(user.points_total || 0);
    if (nextPoints !== currentPoints) {
      void refreshUser();
    }
  }, [program?.vip, refreshUser, status, user]);

  const availablePoints = Number(user?.points_balance ?? 0);
  const totalPoints = Number(user?.points_total ?? program?.vip?.current_points ?? user?.points_balance ?? 0);
  const metricCards = useMemo(
    () => [
      {
        label: t("home.bwMetrics.videos"),
        value: String(submissions.filter(isRecentSubmission).length),
      },
      {
        label: t("home.bwMetrics.average"),
        value: String(averageScore(submissions)),
      },
      {
        label: t("home.bwMetrics.points"),
        value: totalPoints.toLocaleString(),
      },
    ],
    [submissions, t, totalPoints],
  );

  return (
    <div className="bw-app bw-app--upload">
      <BwTopNav active="upload" user={user} points={user ? availablePoints : undefined} />

      <main className="bw-page bw-page--upload">
        <section className="bw-upload-hero">
          <h1>{t("home.bwTitle")}</h1>
          <p>{t("home.bwBody")}</p>

          <MonoUploadComposer
            signedIn={status === "authenticated"}
            token={token}
            onRequireAuth={() => openAuthModal("signin")}
            onSubmissionQueued={() => undefined}
            onProgressChange={setProgress}
          />

          {status === "authenticated" && (progress.step > 1 || progress.jobId) ? <MonoProgressCard {...progress} /> : null}
        </section>

        <section className="bw-metric-strip">
          {metricCards.map((item) => (
            <article key={item.label} className="bw-metric-card">
              <strong>{item.value}</strong>
              <span>{item.label}</span>
            </article>
          ))}
        </section>
      </main>
      <FloatingViaCat />
    </div>
  );
}
