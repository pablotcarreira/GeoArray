# Pablo Carreira - 08/03/17
from typing import Iterator, List, Tuple, Union, Sequence

import numpy as np
from osgeo import gdal, osr

from geodata.geo_objects import BBox, RasterDefinition
from geodata.srs_utils import create_osr_srs


class RasterData:
    """Representa uma matriz raster com características espaciais."""
    # Aguardando python 3.6 para poder usar o typing aqui. Ficaria assim:
    # n_channels: int = 1 ou apenas n_channels: int

    _block_list = None
    _block_indices = None
    n_channels = 1
    proj = None
    origem = None
    pixel_size = None
    gdal_dataset = None

    def __init__(self, img_file: Union[str, gdal.Dataset], write_enabled: bool = False, verbose: bool = False):
        """
        :param img_file: Caminho para o arquivo tiff da imagem ou um gdal dataset.
        :param write_enabled: Habilita a escrita para o arquivo. 
        """
        #: Caminho para o arquivo tiff da imagem (fonte de dados).
        self.verbose = verbose
        self.img_file = img_file
        self.write_enabled = write_enabled

        if isinstance(img_file, gdal.Dataset):
            self.gdal_dataset = img_file
            self.src_image = None
        elif isinstance(img_file, str):
            self.src_image = img_file
        else:
            raise TypeError("Wrong type for img_file, must be str or gdal.Dataset.")

        self._load_metadata()  # May be lazy?

    @property
    def raster_definition(self):
        """Retorna o objeto RasterDefinition com as características deste raster."""
        return RasterDefinition(self.rows, self.cols, self.origem[0], self.origem[1], self.pixel_size, -self.pixel_size, self.wkt_srs)

    def compare(self, other: "RasterData") -> None:
        """Prints a comparison of this and other RasterData"""
        print("Rows", self.rows, other.rows)
        print("Cols", self.cols, other.cols)
        print("Block size", self.block_size, other.block_size)
        print("Origin", self.origem, other.origem)
        print("Pixel size", self.pixel_size, other.pixel_size)

    def __eq__(self, other: 'RasterData') -> bool:
        """Check equality based on rols, cols, origin, pizel size
        and spatial reference system. Don't check block size.

        :param other: Other RasterData
        :return:
        """
        # Estas 3 linhas garantem a comparação correta da referência espacial.
        spatial_self = osr.SpatialReference(self.proj)
        spatial_other = osr.SpatialReference(other.proj)
        same_spatial = spatial_self.IsSame(spatial_other)
        return (self.rows == other.rows and
                self.cols == other.cols and
                self.origem == other.origem and
                self.pixel_size == other.pixel_size and
                same_spatial)

    @classmethod
    def create(cls, img_file: str, rows: int, cols: int, pixel_size: Union[int, float, Sequence],
               xmin: float, ymax: float, bands=1, data_type=gdal.GDT_Float32, memoria=False):
        """Creates a new raster on the disk and returns it."""
        if isinstance(pixel_size, (int, float)):
            pixel_size = (pixel_size, -pixel_size)
        if memoria:
            gdal_driver = gdal.GetDriverByName('MEM')
            img_file = "MEM"
        else:
            gdal_driver = gdal.GetDriverByName('GTiff')
        raster = gdal_driver.Create(img_file, cols, rows, bands, data_type)
        if raster is None:
            raise RuntimeError("Error creating Gdal raster.")
        raster.SetGeoTransform((xmin, pixel_size[0], 0, ymax, 0, pixel_size[1]))
        if memoria:
            return cls(raster, write_enabled=True)
        else:
            del raster
            return cls(img_file, write_enabled=True)

    @property
    def shape(self) -> Tuple[int, int]:
        """Image shape (rows, cols).
        Across this program, shapes are aways in "C order": rows x cols, or Y x X or H x W. 
        """
        return self.rows, self.cols

    def change_resolution(self, new_pixel_size: float, out_image: str="", memory: bool=False) -> "RasterData":
        """ Change the real world size of the image pixel."""
        options = gdal.WarpOptions(xRes=new_pixel_size,
                                   yRes=new_pixel_size,
                                   targetAlignedPixels=True,
                                   format="MEM" if memory else "GTiff")
        if not memory and out_image == "":
            raise ValueError("Must provide an output image name for gtiff.")
        return RasterData(gdal.Warp(out_image, self.gdal_dataset, options=options))

    def read_all(self) -> np.ndarray:
        """Reads the entire data into an array."""
        return self.gdal_dataset.ReadAsArray()

    def read_block_by_coordinates(self, y0, y1, x0, x1):
        """Get a block by image coordinates.
        Returns a RGB block.
        
        Remember: Row first!
         
        :param y0: Y start.
        :param y1: Y end.
        :param x0: X start.
        :param x1: X end.         
        """
        # Make sure the params are ints otherwise gdal won't accept them.
        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        # Gdal takes offset and size instead of start and end, so we convert the parameters.
        x_size = x1 - x0
        y_size = y1 - y0
        channels_count = range(self.n_channels)
        channels_blocks = []
        for item in channels_count:
            channel = self.gdal_dataset.GetRasterBand(item + 1)
            channels_blocks.append(channel.ReadAsArray(x0, y0, x_size, y_size))
        return np.dstack(channels_blocks)

    def get_bbox_position_within_image(self, other_bbox: BBox, allow_partial: bool=False, allow_any_srs=False):
        """Claculate the position of a bbox within the image (in pixels).

        Both must be in the same coordinate system.

        :param other_bbox: Other bbox.
        :param allow_partial: Allows the function to return partial coverage, if not raises RuntimeError.
        """
        this_bbox = self.get_bbox()
        pixel_size = self.pixel_size

        # Create SRS for comparison.
        this_srs, other_srs = osr.SpatialReference(), osr.SpatialReference()
        this_srs.ImportFromWkt(this_bbox.wkt_srs)
        other_srs.ImportFromWkt(other_bbox.wkt_srs)

        if not this_srs.IsSame(other_srs) and not allow_any_srs:
            raise RuntimeError("Must be in the same SRS.")

        # Detect out of bounds:
        out_of_bounds = False
        if other_bbox.xmax <= this_bbox.xmin:
            out_of_bounds = True
        elif other_bbox.xmin >= this_bbox.xmax:
            out_of_bounds = True
        elif other_bbox.ymax <= this_bbox.ymin:
            out_of_bounds = True
        elif other_bbox.ymin >= this_bbox.ymax:
            out_of_bounds = True
        if out_of_bounds:
            raise RuntimeError("Bbox totaly out of bounds.")

        # Situations where the other bbox is partially covering this bbox.
        origin_y, origin_x = None, None
        displacement_h, displacement_v = None, None
        patial = False
        
        obb_ymax = other_bbox.ymax
        obb_ymin = other_bbox.ymin
        obb_xmax = other_bbox.xmax
        obb_xmin = other_bbox.xmin
        
        if obb_ymax > this_bbox.ymax:
            obb_ymax = this_bbox.ymax
            origin_y = this_bbox.ymax
            displacement_v = 0
            patial = True
        if obb_ymin < this_bbox.ymin:
            obb_ymin = this_bbox.ymin
            patial = True
        if obb_xmax > this_bbox.xmax:
            obb_xmax = this_bbox.xmax
            patial = True
        if obb_xmin < this_bbox.xmin:
            obb_xmin = this_bbox.xmin
            origin_x = this_bbox.xmin
            displacement_h = 0
            patial = True

        if patial and not allow_partial:
            raise RuntimeError("Other bbox partially out of the image extent, you may use allow_partial=True.")

        # Como a imagem tem origem no topo esquerdo, o deslocamento deve ser da esquerda e do topo:
        # floor é usado para que sobre parte de um pixel para cima e para a esquerda, em vez de faltar.
        # Isso é aplicado apenas quando a área de interesse não excede as bordas.
        if displacement_h is None:
            displacement_h = int((obb_xmin - this_bbox.xmin) / pixel_size)
        if displacement_v is None:
            displacement_v = int((this_bbox.ymax - obb_ymax) / pixel_size)

        # Como é feito este mini deslocamento, passamos a usar estas novas coordenadas como
        # coordenadas de origem do pedaço (apenas quando o pedaço ainda não foi determinado):
        if origin_x is None:
            origin_x = this_bbox.xmin + displacement_h * pixel_size
        if origin_y is None:
            origin_y = this_bbox.ymax - displacement_v * pixel_size  # no topo esquerdo, invertido.
        # (lembre-se que a o sistema de referência (SR) da imagem tem origem no canto superior esquerdo e o geográfico
        # no canto inferior esquerdo.

        block_width = int((obb_xmax - origin_x) / pixel_size) - 1  # FIXME: -1 Mágico.
        block_height = int((origin_y - obb_ymin) / pixel_size)

        # origem_y = origem_y - altura_px_pedaco * tamanho_pixel  # no topo esquedo, correto.

        #        0 dish,          1disv,        2 width,     3 heigth,     4 orx,     5ory
        return displacement_h, displacement_v, block_width, block_height, origin_x, origin_y

    def read_block_by_utm_coordinates(self, xu0, xu1, yu0, yu1):
        """Get a block by utm coordinates.
        Returns a RGB block.
        The mission here is to tranform meters in pixel coordinates that can be accepted by read_block_by_coordinates.
        """
        ox, oy = self.origem
        res = self.pixel_size
        coords = [round((oy - yu1) / res),  # y0
                  round((oy - yu0) / res),  # y1
                  round((xu0 - ox) / res),  # x0
                  round((xu1 - ox) / res),  # x1
                  ]
        # Debug:
        # ty = coords[1] - coords[0]
        # tx = coords[3] - coords[2]
        return self.read_block_by_coordinates(*coords)

    # noinspection PyTypeChecker
    @property
    def block_indices(self) -> np.ndarray:
        """Contêm o índice dos elementos de qualquer bloco. É lazy."""
        if self._block_indices is None:
            self._block_indices = np.indices(self.block_size)
        return self._block_indices

    @property
    def block_list(self) -> List:
        """Propriedade lazy contendo a lista de blocos."""
        if not self._block_list:
            self._block_list = self._create_blocks_list()
        return self._block_list

    def get_bbox(self):
        """Pega o bbox da imagem."""
        xmax = self.origem[0] + self.cols * self.pixel_size
        ymin = self.origem[1] - self.rows * self.pixel_size
        return BBox(self.origem[0], ymin, xmax, self.origem[1], wkt_srs=self.wkt_srs)

    def _load_metadata(self):
        """Lê meta informações do arquivo."""
        if isinstance(self.gdal_dataset, gdal.Dataset):
            gdal_dataset = self.gdal_dataset
        else:
            gdal_dataset = gdal.Open(self.src_image, gdal.GA_Update if self.write_enabled else gdal.GA_ReadOnly)
            if not gdal_dataset:
                raise IOError("Erro ao abrir o arquivo ou arquivo inexistente: " + self.src_image)
            self.gdal_dataset = gdal_dataset

        # Informacoes gerais.
        self.cols = gdal_dataset.RasterXSize
        self.rows = gdal_dataset.RasterYSize
        self.n_channels = gdal_dataset.RasterCount
        self.proj = gdal_dataset.GetProjection()

        geot = gdal_dataset.GetGeoTransform()
        self.origem = (geot[0], geot[3])
        self.pixel_size = geot[1]

        src_band = gdal_dataset.GetRasterBand(1)
        self.block_size = src_band.GetBlockSize()

        # Informações por banda.
        for item in range(self.n_channels):
            src_band = gdal_dataset.GetRasterBand(item + 1)
            src_block_size = src_band.GetBlockSize()
            if self.verbose:
                print("Banda {} - Block shape {}x{}px.".format(item + 1, *src_block_size))

    def _create_blocks_list(self):
        """Cretes a list of block reading coordinates."""
        blk_width, blk_height = self.block_size

        # Get the number of blocks.
        x_blocks = int((self.cols + blk_width - 1) / blk_width)
        y_blocks = int((self.rows + blk_height - 1) / blk_height)
        # print("Creating blocks list with {} blocks ({} x {}).".format(
        #     x_blocks * y_blocks, x_blocks, y_blocks))

        blocks_list = []
        for block_column in range(0, x_blocks):
            # Recalculate the shape of the rightmost block.
            if block_column == x_blocks - 1:
                valid_x = self.cols - block_column * blk_width
            else:
                valid_x = blk_width
            xoff = block_column * blk_width
            # loop through Y lines
            for block_row in range(0, y_blocks):
                # Recalculate the shape of the final block.
                if block_row == y_blocks - 1:
                    valid_y = self.rows - block_row * blk_height
                else:
                    valid_y = blk_height
                yoff = block_row * blk_height
                blocks_list.append((xoff, yoff, valid_x, valid_y))
        return blocks_list

    def get_blocks_array_indices(self) -> List:
        """Create a list of array indices (i, j) for the first two dimensions of an array.
        The format is different from numpy.indices, 
        numpy indices creates indices like [[i0, i1, in...], [j0, j1, jn, ...]]
        this functions creates [[i0, j0], [i1, j1], [in, jn], ...]
        
        :returns: A list of indices (e.g. [[i0, j0], [i1, j1], [in, jn], ...])
        """
        # TODO: Make a lazy property.
        array = self.get_blocks_positions_coordinates()
        shape = array.shape
        indices = []
        for irow in range(shape[0]):
            for icol in range(shape[1]):
                indices.append((irow, icol))
        return indices

    def get_iterator(self, banda: int = 1) -> Iterator:
        """Retorna um iterator sobre a imagem, retornando um pedaço do
        tamanho do block size a cada passo.
        
        Notar que os blocos são enviados em ordem diferente do numpy.

        :param banda: Banda da imagem para gerar o iterator.
        """
        blocks_list = self.block_list
        src_band = self.gdal_dataset.GetRasterBand(banda)
        for block in blocks_list:
            # print("Block from list: {}".format(block))
            block_data = src_band.ReadAsArray(*block)
            yield block_data

    def get_rgb_iterator(self, stack: bool = True) -> Iterator:
        """Retorna um iterator sobre os 3 canais (RGB)
        
        :param stack: Empilha os canais em uma matriz (w, h, 3).
        """
        # FIXME: Em vez de repetir o código, apenas chamar o get iterator para cada banda.
        blocks_list = self.block_list
        red_channel = self.gdal_dataset.GetRasterBand(1)
        green_channel = self.gdal_dataset.GetRasterBand(2)
        blue_channel = self.gdal_dataset.GetRasterBand(3)

        for block in blocks_list:
            red_block_data = red_channel.ReadAsArray(*block)
            green_block_data = green_channel.ReadAsArray(*block)
            blue_block_data = blue_channel.ReadAsArray(*block)
            if stack:
                yield np.dstack((red_block_data, green_block_data, blue_block_data))
            else:
                yield red_block_data, green_block_data, blue_block_data

    def clone_empty(self, new_img_file: str, bandas: int = 0, data_type=gdal.GDT_Byte, bits=None) -> 'RasterData':
        """Cria uma nova imagem RasterData com as mesmas características desta imagem,
        a nova imagem é vazia e pronta para a escrita.
        A finalidade é criar imagens para a saída de processamentos.
        São copiados os tamanhos, progeção, geo transform e bandas.

        Caso as bandas não sejam sefinidas usa o numero de bandas desta imagem.

        Não é possível determinar o block size das camadas de saída, portanto a escrita
        ocorre de forma menos eficiente nas imagens criadas quando iteradas com imagens
        obtidas que não possuem tamanho padrão de block (tiled vs. scanline).
        """
        # Criado na unha para poder ajustar parâmetros.

        if bandas == 0:
            bandas = self.n_channels

        gdal_driver = self.gdal_dataset.GetDriver()

        out_block_size = [256, 256]

        # Check if block size is power of two, if not keep the default block size:
        if self.block_size[0] != 0 and ((self.block_size[0] & (self.block_size[0] - 1)) == 0):
            out_block_size[0] = self.block_size[0]
        if self.block_size[1] != 1 and ((self.block_size[1] & (self.block_size[1] - 1)) == 0):
            out_block_size[1] = self.block_size[1]

        if bits is not None:
            geotiff_options = ["NBITS=1",
                               "TILED=YES",
                               "BLOCKXSIZE=" + str(out_block_size[0]),
                               "BLOCKYSIZE=" + str(out_block_size[1])]
        else:
            geotiff_options = ["TILED=YES",
                               "BLOCKXSIZE=" + str(out_block_size[0]),
                               "BLOCKYSIZE=" + str(out_block_size[1])]

        new_dataset = gdal_driver.Create(new_img_file,
                                         self.cols, self.rows,
                                         bands=bandas,
                                         eType=data_type,
                                         options=geotiff_options)

        # Copia as informações georreferenciadas.
        new_dataset.SetProjection(self.gdal_dataset.GetProjection())
        new_dataset.SetGeoTransform(self.gdal_dataset.GetGeoTransform())

        new_dataset.FlushCache()  # Garante a escrita no disco.
        return RasterData(new_img_file, write_enabled=True)

    def get_blocks_positions_coordinates(self) -> np.ndarray:
        """Creates an array containing the coordinates for the position (in image pixels) of each block.        

        Coordinates are: y0, y1, x0, x1
        """
        # TODO: Make a lazy property.
        # Quantos blocos inteiros cabem, quantos pixels sobram
        n_block_rows, resto_pixel_rows = divmod(self.rows, self.block_size[0])
        n_block_cols, resto_pixel_cols = divmod(self.cols, self.block_size[1])
        # Caso haja um pedaço sobrando do final, inclui mais um bloco.
        if resto_pixel_rows != 0:
            n_block_rows += 1
        if resto_pixel_cols != 0:
            n_block_cols += 1
        # Primeiro, cria a matriz de escrita, o bloco tem o tamanho do blk_size.
        coord_array = np.empty((n_block_rows, n_block_cols, 4), dtype=np.uint16)
        for row_index in range(n_block_rows):
            y0 = row_index * self.block_size[0]
            if row_index + 1 == n_block_rows:  # Last row.
                y1 = self.rows
            else:
                y1 = self.block_size[0] * (row_index + 1)

            for col_index in range(n_block_cols):
                x0 = col_index * self.block_size[1]
                if col_index + 1 == n_block_cols:  # Last column.
                    x1 = self.cols
                else:
                    x1 = self.block_size[1] * (col_index + 1)
                coord_array[row_index][col_index] = [y0, y1, x0, x1]
        return coord_array

    def get_block_pixel_coordinates(self, block_index: int) -> np.ndarray:
        """Retorna uma matriz com as coordenadas geográficas dos pixels do bloco.

        :param block_index: 
        """
        block_position = self.block_list[block_index]
        block_coords = np.empty(self.block_indices.shape)
        for eixo in range(self.block_indices.shape[0]):
            coords = self.block_indices[eixo] * self.pixel_size + self.origem[eixo] + \
                     block_position[eixo] * self.pixel_size
            block_coords[eixo] = coords
        return block_coords

    def write_block(self, data_array: np.ndarray, block_index: int, channel: int = 1):
        """Escreve um bloco de dados em uma banda.

        :param channel: 
        :param data_array: 
        :param block_index: O índice do bloco para escrever.
        """
        block_position = self.block_list[block_index]
        self.gdal_dataset.GetRasterBand(channel).WriteArray(data_array, block_position[0], block_position[1])
        self.gdal_dataset.FlushCache()

    def write_all(self, data_array: np.ndarray, channel: int = 1):
        """Write an array to the image starting from the first position."""
        self.gdal_dataset.GetRasterBand(channel).WriteArray(data_array)
        self.gdal_dataset.FlushCache()

    def set_srs(self, srs: Union[osr.SpatialReference, int, str]):
        """Set the spatial reference system for this instance."""
        srs = create_osr_srs(srs)
        self.gdal_dataset.SetProjection(srs.ExportToWkt())
        self.proj = self.gdal_dataset.GetProjection()

    @property
    def wkt_srs(self):
        return self.gdal_dataset.GetProjection()

    def reproject(self, out_image: str, dst_srs: Union[osr.SpatialReference, int, str],
                  memory: bool=False)->"RasterData":
        """Changes this dataset projection and creates a new file.
        Returns a new RasterData referencing the new file.

        :param out_image:
        :param dst_srs:
        :param memory: Use memory driver.
        """
        # Ver docstring para mais opções.
        srs = create_osr_srs(dst_srs)
        if memory:
            return RasterData(gdal.Warp("MEM", self.gdal_dataset, dstSRS=srs, format="MEM"))
        else:
            gdal.Warp(out_image, self.gdal_dataset, dstSRS=srs)
            return RasterData(out_image)
