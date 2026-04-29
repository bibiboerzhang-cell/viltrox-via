import { useEffect } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";

import { fetchStudentClaim } from "../../services/student.service";

export default function StudentSignupRoute() {
  const navigate = useNavigate();
  const [params] = useSearchParams();

  const qrId = params.get("qr_id") ?? "";
  const claimToken = params.get("claim") ?? "";
  const signature = params.get("sig") ?? "";
  const directVid = params.get("student_id") ?? params.get("vid") ?? "";

  useEffect(() => {
    let active = true;

    async function redirectToAuthModal() {
      const next = new URLSearchParams();
      next.set("auth", "register");
      if (qrId) {
        next.set("qr_id", qrId);
      }
      if (directVid) {
        next.set("student_id", directVid.trim().toUpperCase());
      }

      if (!directVid && qrId && claimToken && signature) {
        try {
          const response = await fetchStudentClaim(qrId, claimToken, signature);
          const vid = response.public_claim_id ?? response.prefilled?.school_student_id ?? response.prefilled?.student_id ?? "";
          if (vid) {
            next.set("student_id", String(vid).trim().toUpperCase());
          }
          if (response.prefilled?.email) {
            next.set("email", String(response.prefilled.email).trim().toLowerCase());
          }
        } catch (error) {
          next.set("error", error instanceof Error ? error.message : "Student QR claim unavailable");
        }
      }

      if (active) {
        const studentId = next.get("student_id") ?? "";
        const email = next.get("email") ?? "";
        window.dispatchEvent(
          new CustomEvent("viltrox:open-auth", {
            detail: {
              mode: "register",
              studentId,
              email,
              qrId,
              key: `student-signup:${qrId}:${studentId}:${email}`,
            },
          }),
        );
        navigate(`/?${next.toString()}`, { replace: true });
      }
    }

    void redirectToAuthModal();
    return () => {
      active = false;
    };
  }, [claimToken, directVid, navigate, qrId, signature]);

  return (
    <div className="bw-app bw-app--vid">
      <div className="muted-block">Opening Viltrox 2.0...</div>
    </div>
  );
}
