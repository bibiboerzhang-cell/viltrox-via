import os

from app.core.logging import get_logger

logger = get_logger(__name__)

# 基础路径设定
BASE_DIR = os.path.join(os.getcwd(), "equipment", "Viltrox")

# 终极严谨版设备分类字典（电影组卡口已严格锁死）
EQUIPMENT_TREE = {
    "Lenses": {
        "LAB_Series": {
            "35mm_F1.2": ["E-Mount", "Z-Mount"],
            "135mm_F1.8": ["E-Mount", "Z-Mount"]
        },
        "Pro_Series": {
            "16mm_F1.8": ["E-Mount", "Z-Mount", "L-Mount"],
            "27mm_F1.2": ["E-Mount", "Z-Mount", "X-Mount"],
            "50mm_F1.4": ["E-Mount", "Z-Mount"],
            "56mm_F1.2": ["E-Mount", "Z-Mount", "X-Mount"],
            "75mm_F1.2": ["E-Mount", "Z-Mount", "X-Mount"],
            "85mm_F1.4": ["Z-Mount"]
        },
        "Air_Series": {
            "9mm_F2.8": ["E-Mount", "Z-Mount"],
            "20mm_F2.8": ["E-Mount", "Z-Mount", "L-Mount"],
            "40mm_F2.5": ["E-Mount", "Z-Mount"],
            "50mm_F2.0": ["E-Mount", "Z-Mount"]
        },
        "Cine_Series": {
            # 严格区分 Luna 系列的不同画幅与卡口
            "Luna_30_300mm_T4.0_FF": ["PL-Mount"],
            "Luna_42_420mm_T5.6_65Format": ["LPL-Mount"],
            # EPIC 变形宽银幕：锁死 PL 卡口，摒弃 E/L
            "EPIC_Anamorphic": ["PL-Mount"], 
            "ZMOVE": ["PL-Mount"]
        },
        "DJI_DL_Mount": {
            "Raze_AF_Set": ["DL-Mount"],
            "90mm_F3.5": ["DL-Mount"]
        }
    },
    "Lighting": {
        "Flash": ["Vintage_Z1", "Vintage_Z2_Mini", "Vintage_Z3"],
        "COB_Light": ["Ninja_10", "Ninja_200", "Ninja_300", "Ninja_400"],
        "Panel_Stick_Light": ["Sprite_15C", "Sprite_40", "Retro_08X", "K21_Stick", "H18_Mini"]
    },
    "Monitors": {
        "DC_Series": ["DC-A12800", "DC-X", "DC-V1", "DC-550"]
    },
    "Accessories": {
        "Adapters": ["PL-E", "PL-DL", "EF-E_II", "EF-Z2", "TC-2.0X"],
        "Focus_System": ["NexusFocus_F1"],
        "Diopter": ["EPIC_Diopter"]
    }
}

def create_dirs():
    logger.info("equipment_dir_build_start", extra={"base_dir": BASE_DIR})
    count = 0
    for category, subcats in EQUIPMENT_TREE.items():
        if isinstance(subcats, dict):
            for series, models in subcats.items():
                if isinstance(models, dict):
                    # 针对镜头：分类 -> 系列 -> 焦段 -> 卡口
                    for model, mounts in models.items():
                        for mount in mounts:
                            dir_path = os.path.join(BASE_DIR, category, series, model, mount)
                            os.makedirs(dir_path, exist_ok=True)
                            count += 1
                else:
                    # 针对没有卡口区分的子类
                    for item in models:
                        dir_path = os.path.join(BASE_DIR, category, series, item)
                        os.makedirs(dir_path, exist_ok=True)
                        count += 1
        else:
            logger.warning("equipment_dir_structure_unprocessed", extra={"category": category})

    logger.info("equipment_dir_build_complete", extra={"count": count, "base_dir": BASE_DIR})

if __name__ == "__main__":
    create_dirs()
    
