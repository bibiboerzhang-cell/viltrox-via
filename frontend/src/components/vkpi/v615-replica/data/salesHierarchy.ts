// @ts-nocheck
// Verbatim from vkpi_v6.15.7_integrated.html

export const SALES_HIERARCHY = {
  "United States": {
    lat: 37.7, lng: -95.7, code: "US", revenue: "$1.86M",
    cities: {
      "New York":     { lat: 40.71, lng: -74.00, revenue: "$799k", stores: [
        { name: "B&H Photo Video", lat: 40.7557, lng: -73.9925, revenue: "$487k", type: "Retail",
          venues: [
            { name: "Pro Video Dept · Floor 2", lat: 40.7557, lng: -73.9925, type: "Counter", note: "Viltrox cine lens display" },
            { name: "Photo Lens Aisle · Floor 1", lat: 40.7558, lng: -73.9923, type: "Aisle", note: "AF series demo area" },
          ]
        },
        { name: "Adorama",         lat: 40.7416, lng: -73.9881, revenue: "$312k", type: "Retail", venues: [] },
      ]},
      "Seattle":      { lat: 47.61, lng: -122.33, revenue: "$876k", stores: [
        { name: "Amazon US",       lat: 47.6062, lng: -122.3321, revenue: "$876k", type: "Marketplace", venues: [] },
      ]},
    }
  },
  "China": {
    lat: 35.8, lng: 104.1, code: "CN", revenue: "$434k",
    cities: {
      "上海 / Shanghai":  { lat: 31.23, lng: 121.47, revenue: "$245k", stores: [
        { name: "京东自营 Shanghai", lat: 31.2304, lng: 121.4737, revenue: "$245k", type: "Marketplace", venues: [] },
      ]},
      "杭州 / Hangzhou":  { lat: 30.27, lng: 120.15, revenue: "$189k", stores: [
        { name: "天猫国际",          lat: 30.2741, lng: 120.1551, revenue: "$189k", type: "Marketplace", venues: [] },
      ]},
    }
  },
  "Germany": {
    lat: 51.1, lng: 10.4, code: "DE", revenue: "$121k",
    cities: {
      "Düsseldorf":  { lat: 51.2277, lng: 6.7735, revenue: "$67k", stores: [
        { name: "Foto Koch",       lat: 51.2277, lng: 6.7735, revenue: "$67k", type: "Retail", venues: [] },
      ]},
      "Berlin":      { lat: 52.5200, lng: 13.4050, revenue: "$54k", stores: [
        { name: "Calumet Photo Berlin", lat: 52.5208, lng: 13.4094, revenue: "$54k", type: "Retail", venues: [] },
      ]},
    }
  },
  "United Kingdom": {
    lat: 55.3, lng: -3.4, code: "GB", revenue: "$78k",
    cities: {
      "London": { lat: 51.5074, lng: -0.1278, revenue: "$78k", stores: [
        { name: "WEX Photo Video", lat: 51.5246, lng: -0.0900, revenue: "$78k", type: "Retail", venues: [] },
      ]},
    }
  },
  "Japan": {
    lat: 36.2, lng: 138.2, code: "JP", revenue: "$132k",
    cities: {
      "Tokyo / 東京": { lat: 35.6762, lng: 139.6503, revenue: "$132k", stores: [
        { name: "Map Camera Tokyo", lat: 35.6938, lng: 139.7035, revenue: "$132k", type: "Retail", venues: [] },
      ]},
    }
  },
};
