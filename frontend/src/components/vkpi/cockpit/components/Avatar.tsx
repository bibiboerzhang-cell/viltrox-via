// Verbatim from vkpi_v6.15.7_integrated.html


import { useState } from "react";

export function Avatar({ src, alt, size = 32, fallback, gradient }: any) {
  const [errored, setErrored] = useState(false);
  const initials = (fallback || (alt ? alt.replace(/^@/, "").slice(0, 2).toUpperCase() : "?"));
  if (errored || !src) {
    return (
      <div
        className="flex items-center justify-center rounded-full text-[10px] font-medium text-white"
        style={{ width: size, height: size, background: gradient || "linear-gradient(135deg, #8b5cf6, #3b82f6)" }}
        aria-label={alt}
      >
        {initials}
      </div>
    );
  }
  return (
    <img
      src={src}
      alt={alt}
      onError={() => setErrored(true)}
      className="rounded-full border border-white/20 object-cover"
      style={{ width: size, height: size }}
    />
  );
}
