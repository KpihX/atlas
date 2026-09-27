type Props = {
  enabled: boolean;
  text: string;
  companionName: string;
};

export function AtlasSubtitles({ enabled, text, companionName }: Props) {
  if (!enabled || !text) return null;
  return (
    <div className="atlas-subtitles" role="status" aria-live="polite">
      <span>{companionName}</span>
      <p>{text}</p>
    </div>
  );
}
