interface GlassProfileProps {
  initial?: string;
  name?: string;
  role?: string;
}

export function GlassProfile({ initial = 'J', name = 'Jianbo', role = 'Marketing Director' }: GlassProfileProps) {
  return (
    <div className="profile">
      <div className="avatar">{initial}</div>
      <div><b>{name}</b><p>{role}</p></div>
      <div className="chev">⌄</div>
    </div>
  );
}
