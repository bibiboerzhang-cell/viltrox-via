// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html


import React, { useEffect, useState } from "react";
import { animate, useMotionValue } from "framer-motion";

const e = React.createElement;

export function AnimatedNumber({ value, format = (v) => v }) {
  const [display, setDisplay] = useState(0);
  const motionVal = useMotionValue(0);
  useEffect(() => {
    const n = typeof value === "string" ? parseFloat(value.replace(/[^0-9.-]/g, "")) || 0 : value;
    const controls = animate(motionVal, n, { duration: 1.2, ease: [0.16, 1, 0.3, 1], onUpdate: setDisplay });
    return () => controls.stop();
  }, [value]);
  return format(display);
}
