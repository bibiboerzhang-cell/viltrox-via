// Compatibility entrypoint for existing Cockpit modules.
// The single language source now lives above the router in AppProviders.
export {
  I18nContext,
  makeT,
  useT,
} from "../../../../app/providers/LocaleProvider";
