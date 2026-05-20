import { DashboardPremium } from './DashboardPremium';

interface GlassDemoPageProps {
  apiToken?: string;
  userName?: string;
  userRole?: string;
}

export function GlassDemoPage(props: GlassDemoPageProps) {
  return <DashboardPremium {...props} testId="vkpi-glass-demo" />;
}

export default GlassDemoPage;
