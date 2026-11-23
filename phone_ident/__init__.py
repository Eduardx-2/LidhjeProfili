"""Identificación conservadora de dispositivos y Bayes configurable, sin dependencias."""

from .core import BayesModel, PhoneData, identify_device, identify_phone

__all__ = ["BayesModel", "PhoneData", "identify_device", "identify_phone"]
