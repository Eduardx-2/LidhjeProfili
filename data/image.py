class ImageData:

    def __init__(self,captura_date,captura_date_mili,upload_date,upload_update,uri):
        self.capturaData = captura_date
        self.capturaMiliSegundos = captura_date_mili
        self.uploadDate = upload_date
        self.uploadUpd = upload_update
        self.uri = uri
        self.data_json = []
    

    def createInfoseidd(self):
        self.data_json.append(
                {"capturaImageTiempo": self.capturaData,
                 "capturaMiliSegundos": self.capturaMiliSegundos,
                 "uploadImagenTiempo": self.uploadDate,
                 "uploadActImagen": self.uploadUpd,
                 "uriImage":self.uri}
                )
        return self.data_json
